"""RED-first real-factory tests for the reviewed-basis origin of a Rule 9a carry (Lead 2026-11-09T09:08:15Z).

Built on corpus ec33fb6a. The factory under test is the one Tools proposed at 2026-11-09T09:01:14Z
(derive_live_carry_forward: it reads the complete queue and a fresh strict live PR snapshot itself and accepts the
reviewed basis only from the original approvals, directly or through a canonical event-bound descriptor). Its
target, parameters and result shape live in tests/fixtures/bridge_consensus_carry_forward/origin_factory_cases.json,
the single adaptation point once Tools publishes or refines the API.

Real git histories come from the corpus builders (legit rebase, side-commit base substitution, RCO2 stacked
ancestry); the strict live snapshot comes from the synthetic GitHub of the CI snapshot tests. Nothing is patched.

Failure classes: "RED: missing API: ..." until the factory exists (never verified behaviour); any other failure means
the factory decided differently from the recorded expectation. The GREEN controls prove the harness: the recorded
basis reproduces the git facts and basis-carrying approvals stay ordinary exact-head approvals today.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import inspect
import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.idle_consensus_auto_merge import verify_bridge_consensus

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "origin_factory_cases.json")
                   .read_text(encoding="utf-8"))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = _load("fable5_origin_corpus_harness", "test_consensus_carry_forward_adversarial.py")
ci_harness = _load("fable5_origin_ci_harness", "test_consensus_carry_forward_ci_snapshot.py")

REGISTRY = corpus.CANONICAL_REGISTRY
REPO_NAME = "example/repo"
PR = 9301
AUTHOR = "claude-rco-2"
TASK = "claude-rco-2/origin-factory-pr-9301"
TOOLS_TASK = "codex-tools-1/origin-factory-pr-9301"
CHANGED_PATH = "src/feature.txt"
BASIS_SCHEMA, ORIGIN_SCHEMA, SNAPSHOT_SCHEMA = (
    CASES["review_basis"]["schema"], "wd.bridge-review-origin.v1", "wd.bridge-review-snapshot.v1")


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(obj):
    return hashlib.sha256(_canonical(obj)).hexdigest()


# --------------------------------------------------------------------------------------------------------------
# Real git histories from the corpus builders; live_base = the PR base branch tip GitHub would report now
# --------------------------------------------------------------------------------------------------------------
def _from_pair(built):
    repo, (old, old_base), (new, new_base) = built
    return SimpleNamespace(repo=repo, old=old, old_base=old_base, new=new, live_base=new_base, feature=None)


def _from_stacked(built):
    return SimpleNamespace(repo=built["repo"], old=built["approved"]["head"], old_base=built["approved"]["base_sha"],
                           new=built["proposed"]["head"], live_base=built["feature"], feature=built["feature"])


@pytest.fixture(scope="module")
def histories(tmp_path_factory):
    side = _from_pair(corpus.build_side_commit_base_substitution(tmp_path_factory.mktemp("side") / "repo"))
    side.side_base, side.live_base = side.live_base, side.repo.rev("refs/heads/main")
    return {
        "legit": _from_pair(corpus.build_legit_rebase(tmp_path_factory.mktemp("legit") / "repo")),
        "side": side,
        "stacked_rebased": _from_stacked(corpus._stacked(tmp_path_factory.mktemp("stacked_r") / "repo", rebase=True)),
        "stacked_no_rewrite": _from_stacked(corpus._stacked(tmp_path_factory.mktemp("stacked_n") / "repo",
                                                            rebase=False)),
    }


@pytest.fixture(autouse=True)
def anchored_fleet(tmp_path, monkeypatch):
    """Wholly synthetic, test-owned generation pinning the physical git (same model as the corpus fixture)."""
    executable = corpus._physical_git()
    root = tmp_path / "anchored-bundle" / hashlib.sha1(b"fable-5 origin factory generation").hexdigest()
    root.mkdir(parents=True)
    fleet = json.dumps({"schema_version": 2, "git_executable": str(executable)}).encode("ascii")
    (root / "wd-fleet.json").write_bytes(fleet)
    manifest = json.dumps({"source_commit": root.name,
                           "files": {"wd-fleet.json": hashlib.sha256(fleet).hexdigest()}}).encode("ascii")
    (root / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(manifest).hexdigest())


# --------------------------------------------------------------------------------------------------------------
# Reviewed basis, bridge events and descriptors
# --------------------------------------------------------------------------------------------------------------
def _basis(h, *, base=None, head=None, task=TASK, pr=PR):
    base, head = base or h.old_base, head or h.old
    merge_bases = h.repo.merge_bases(base, head)
    assert len(merge_bases) == 1
    identity = h.repo.identity(base, head)
    return {"schema": BASIS_SCHEMA, "repository": REPO_NAME, "pr_number": pr, "task_id": task, "base_ref": "main",
            "head": head, "base_sha": base, "merge_base": merge_bases[0], "diff_sha256": identity["diff_sha256"],
            "patch_id": identity["patch_id"]}


def _event(agent, typ, status, head, minute, *, task=TASK, payload=None):
    body = {"head": head, "pr": PR} if head else {"pr": PR}
    body.update(payload or {})
    return {"ts_utc": "2026-11-09T08:%02d:00Z" % minute, "agent": agent, "agent_uuid": REGISTRY[agent],
            "type": typ, "status": status, "task_id": task, "message": f"{status} at the bound head",
            "payload": body}


def _claim(author=AUTHOR, task=TASK):
    event = _event(author, "claim", "active", None, 0, task=task)
    event["message"], event["write_scope"] = "claim for the PR task", [CHANGED_PATH]
    return event


VOTERS = [("codex-lead-1", "decision", "build_consensus_pass"), ("codex-tools-1", "decision", "build_consensus_pass"),
          ("claude-rco-1", "decision", "rco_pass")]


def _approvals(h, *, bind=(), basis=None, task=TASK, skip=(), commit=(), commit_digest=None):
    """bind: full basis + digest in the vote (direct route); commit: only the vote's own review_basis_sha256
    commitment (descriptor route); commit_digest overrides one voter's committed digest."""
    basis, commit_digest = basis or {}, commit_digest or {}
    events = []
    for minute, (agent, typ, status) in enumerate(VOTERS, start=1):
        if agent in skip:
            continue
        extra = None
        if agent in bind:
            b = basis.get(agent) or _basis(h, task=task)
            extra = {"review_basis": b, "review_basis_sha256": _sha(b)}
        elif agent in commit:
            extra = {"review_basis_sha256": commit_digest.get(agent) or _sha(_basis(h, task=task))}
        events.append(_event(agent, typ, status, h.old, minute, task=task, payload=extra))
    return events


def _all_bound(h, **kw):
    return [_claim()] + _approvals(h, bind=[a for a, _, _ in VOTERS], **kw)


def _descriptor(h, tmp_path, approvals, *, name="origin"):
    basis = _basis(h)
    body = {"schema": SNAPSHOT_SCHEMA, "review_basis": basis, "review_basis_sha256": _sha(basis),
            "approval_event_sha256": [corpus.event_sha256(e) for e in approvals]}
    raw = _canonical(body)
    rel = f".review-origin/{tmp_path.name}/{name}.json"
    path = Path(h.repo.path) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": rel, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}, basis, path


def _binding(h, voter, descriptor, basis, approval_refs, minute):
    origin = {"schema": ORIGIN_SCHEMA, "descriptor": descriptor, "review_basis": basis,
              "review_basis_sha256": _sha(basis), "approval_event_sha256": approval_refs}
    return _event(voter, CASES["review_basis"]["binding_event_type"], "review_basis_bound", h.old, minute,
                  payload={"review_origin": origin})


ALL_VOTERS = tuple(a for a, _, _ in VOTERS)


def _descriptor_route(h, tmp_path, *, issuer="codex-lead-1", committed=ALL_VOTERS, commit_digest=None):
    """Descriptor route as refined by Tools 09:15:42Z: each committed original vote carries its own
    review_basis_sha256; ONE packaging review_basis_bound event by the issuer supplies the descriptor (basis preimage
    + exact original refs). The issuer only packages; it can never stand in for a peer's missing commitment."""
    approvals = _approvals(h, commit=committed, commit_digest=commit_digest)
    descriptor, basis, path = _descriptor(h, tmp_path, approvals)
    bindings = [_binding(h, issuer, descriptor, basis, [corpus.event_sha256(e) for e in approvals], 10)]
    return [_claim()] + approvals + bindings, bindings, path


def _events_file(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8")
    return path


def _queue(complete=True):
    def checker(**_kwargs):
        return {"ok": True, "complete": complete, "errors": [],
                "decision": "accepted_queue_complete" if complete else "accepted_queue_incomplete"}
    return checker


def _gh(h, *, task=TASK):
    return ci_harness.FakeGitHub(head=h.new, base=h.live_base, repo=REPO_NAME, pr=PR, branch=task,
                                 changed_path=CHANGED_PATH, author_login="Ahkeratmehilaiset")


# --------------------------------------------------------------------------------------------------------------
# Factory adapter: the single adaptation point once Tools publishes (fixture factory.*)
# --------------------------------------------------------------------------------------------------------------
def _factory():
    target = CASES["factory"]["target"]
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        pytest.fail(f"RED: missing API: {module_name} is not importable ({exc})")
    fn = getattr(module, attr, None)
    if not callable(fn):
        pytest.fail(f"RED: missing API: {target} is not published yet (Tools proposal 09:01:14Z; fixture "
                    f"origin_factory_cases.json factory.target)")
    missing = [p for p in CASES["factory"]["parameters"] if p not in inspect.signature(fn).parameters]
    if missing:
        pytest.fail(f"RED: missing API: {target} lacks the parameters {missing}")
    return fn


def _run(tmp_path, h, events, *, task=TASK, request_extra=None, expected_base=None, gh=None):
    fn = _factory()
    request = {"approved_head": h.old, "repo_root": Path(h.repo.path)}
    request.update(request_extra or {})
    gh = gh or _gh(h, task=task)
    try:
        result = fn(request=request, events_path=_events_file(tmp_path, events), repo=REPO_NAME, pr_number=PR,
                    task_id=task, expected_head=h.new, expected_base_sha=expected_base or h.live_base,
                    runner=gh, accepted_queue_checker=_queue())
    except (ValueError, ci_harness.PrStatusSnapshotError) as exc:
        return SimpleNamespace(carry=False, text=str(exc), result=None, gh=gh)
    assert isinstance(result, Mapping) and result.get("carry") in (True, False), result
    text = json.dumps({k: result.get(k) for k in ("reason", "reasons", "errors", "decision")}, default=str)
    return SimpleNamespace(carry=result["carry"], text=text, result=result, gh=gh)


def _merge_calls(gh):
    return [c for c in gh.calls if c[:3] == ["gh", "pr", "merge"]]


def _assert_carry(case, out, h, *, voters):
    assert CASES["cases"][case]["expect"] == "carry"
    assert out.carry is True, out.text
    assert _merge_calls(out.gh) == [], "the factory derives evidence; it never merges"
    assert any("check-runs" in a for c in out.gh.calls for a in c), "the factory must read the strict live snapshot"
    dumped = json.dumps(out.result, default=str)
    basis = _basis(h)
    assert _sha(basis) in dumped and h.old_base in dumped, "the receipt must retain the historical reviewed basis"
    carried = {row.get("agent") for row in out.result.get("carried", [])}
    assert carried == set(voters), carried


def _assert_refused(case, out):
    spec = CASES["cases"][case]
    assert spec["expect"] == "refuse"
    assert out.carry is False, f"{case}: carried although it must refuse"
    if out.gh is not None:
        assert _merge_calls(out.gh) == []
    patterns = [CASES["reason_patterns"][k] for k in spec["reason"].split("|")]
    assert any(re.search(p, out.text) for p in patterns), (case, out.text)
    if out.result is not None:
        assert not out.result.get("carried"), "a refused carry carries nothing"


# ==============================================================================================================
# GREEN controls (harness proof; green today and after wiring)
# ==============================================================================================================
def test_every_fixture_case_has_a_test():
    tests = [name for name in globals() if name.startswith("test_factory_")]
    assert [c for c in CASES["cases"] if not any(n.startswith("test_factory_" + c) for n in tests)] == []


def test_control_recorded_basis_reproduces_the_git_facts(histories):
    for name, h in histories.items():
        basis = _basis(h)
        identity = h.repo.identity(h.old_base, h.old)
        assert (basis["diff_sha256"], basis["patch_id"]) == (identity["diff_sha256"], identity["patch_id"]), name
        current = h.repo.identity(h.live_base, h.new)
        if name == "legit":
            assert current["diff_sha256"] == basis["diff_sha256"]  # a true content-identical rebase
        else:
            assert current["diff_sha256"] != basis["diff_sha256"], name  # live diff differs from what was reviewed


def test_control_basis_carrying_approvals_stay_exact_head_approvals(histories):
    h = histories["legit"]
    events = _all_bound(h)
    at_old = verify_bridge_consensus(events=events, task_id=TASK, head_sha=h.old, pr_number=PR, author_agent=AUTHOR)
    at_new = verify_bridge_consensus(events=events, task_id=TASK, head_sha=h.new, pr_number=PR, author_agent=AUTHOR)
    assert at_old["ok"] is True, at_old.get("reasons")
    assert at_new["ok"] is False  # without the factory nothing moves to the proposed head


def test_control_descriptor_bytes_match_their_binding(histories, tmp_path):
    h = histories["legit"]
    events, bindings, path = _descriptor_route(h, tmp_path)
    raw = path.read_bytes()
    for binding in bindings:
        descriptor = binding["payload"]["review_origin"]["descriptor"]
        assert descriptor["bytes"] == len(raw) and descriptor["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("voter", ["codex-lead-1", "codex-tools-1", "claude-rco-1"])
def test_control_binding_event_effect_on_exact_head_consensus_today(histories, voter):
    """Recorded observation (fixture review_basis.binding_event_note): with the proposed decision shape, a recognized
    RCO's review_basis_bound posted after its approval reads as an RCO block at that head today; build voters'
    bindings are neutral. If this flips, the gate's reading of RCO decisions changed and needs its own review."""
    h = histories["legit"]
    approvals = _approvals(h)
    binding = _binding(h, voter, {"path": "x", "bytes": 0, "sha256": "0" * 64}, _basis(h),
                       corpus.event_sha256(approvals[[a for a, _, _ in VOTERS].index(voter)]), 10)
    report = verify_bridge_consensus(events=[_claim()] + approvals + [binding], task_id=TASK, head_sha=h.old,
                                     pr_number=PR, author_agent=AUTHOR)
    rco_blocked = voter == "claude-rco-1" and CASES["review_basis"]["binding_event_type"] == "decision"
    assert report["ok"] is (not rco_blocked), report.get("reasons")


# ==============================================================================================================
# RED until the factory is published: expected verdicts
# ==============================================================================================================
def test_factory_valid_all_bound_direct(histories, tmp_path):
    h = histories["legit"]
    out = _run(tmp_path, h, _all_bound(h))
    _assert_carry("valid_all_bound_direct", out, h, voters=["codex-lead-1", "codex-tools-1", "claude-rco-1"])


def test_factory_valid_bound_via_descriptor(histories, tmp_path):
    h = histories["legit"]
    events, bindings, _ = _descriptor_route(h, tmp_path)
    out = _run(tmp_path, h, events, request_extra={"origin_binding_event_sha256": corpus.event_sha256(bindings[-1])})
    _assert_carry("valid_bound_via_descriptor", out, h, voters=["codex-lead-1", "codex-tools-1", "claude-rco-1"])


def test_factory_valid_author_waiver(histories, tmp_path):
    h = histories["legit"]
    events = [_claim("codex-tools-1", TOOLS_TASK)] + _approvals(
        h, bind=["codex-lead-1", "claude-rco-1"], basis={a: _basis(h, task=TOOLS_TASK) for a in
                                                         ("codex-lead-1", "claude-rco-1")},
        task=TOOLS_TASK, skip=["codex-tools-1"])
    out = _run(tmp_path, h, events, task=TOOLS_TASK)
    assert CASES["cases"]["valid_author_waiver"]["expect"] == "carry"
    assert out.carry is True, out.text
    assert _merge_calls(out.gh) == []
    assert {row.get("agent") for row in out.result.get("carried", [])} == {"codex-lead-1", "claude-rco-1"}
    assert "codex-tools-1" in json.dumps(out.result.get("receipt", out.result), default=str)  # waiver recorded


def test_factory_valid_request_base_assertion_matches(histories, tmp_path):
    h = histories["legit"]
    out = _run(tmp_path, h, _all_bound(h), request_extra={"approved_base_sha": h.old_base})
    _assert_carry("valid_request_base_assertion_matches", out, h,
                  voters=["codex-lead-1", "codex-tools-1", "claude-rco-1"])


def test_factory_missing_basis_one_approval(histories, tmp_path):
    h = histories["legit"]
    events = [_claim()] + _approvals(h, bind=["codex-tools-1", "claude-rco-1"])
    _assert_refused("missing_basis_one_approval", _run(tmp_path, h, events))


def test_factory_absent_legacy_anchor(histories, tmp_path):
    h = histories["legit"]
    _assert_refused("absent_legacy_anchor", _run(tmp_path, h, [_claim()] + _approvals(h)))


def _mismatch(h, field, value):
    b = _basis(h)
    b[field] = value
    return b


def test_factory_mismatched_basis_diff(histories, tmp_path):
    h = histories["legit"]
    events = _all_bound(h, basis={"claude-rco-1": _mismatch(h, "diff_sha256", "0" * 64)})
    _assert_refused("mismatched_basis_diff", _run(tmp_path, h, events))


def test_factory_mismatched_basis_base(histories, tmp_path):
    h = histories["legit"]
    events = _all_bound(h, basis={"codex-lead-1": _basis(h, base=h.live_base, head=h.old)})
    _assert_refused("mismatched_basis_base", _run(tmp_path, h, events))


def test_factory_basis_digest_not_matching(histories, tmp_path):
    h = histories["legit"]
    events = _all_bound(h)
    events[2]["payload"]["review_basis_sha256"] = "f" * 64
    _assert_refused("basis_digest_not_matching", _run(tmp_path, h, events))


def test_factory_basis_for_another_pr(histories, tmp_path):
    h = histories["legit"]
    other = {a: _basis(h, pr=PR + 1) for a, _, _ in VOTERS}
    _assert_refused("basis_for_another_pr", _run(tmp_path, h, _all_bound(h, basis=other)))


def test_factory_basis_not_matching_git(histories, tmp_path):
    """Every approval agrees on a self-consistent basis whose diff digest is not the git diff of the approved head."""
    h = histories["legit"]
    forged = _mismatch(h, "diff_sha256", "1" * 64)
    _assert_refused("basis_not_matching_git",
                    _run(tmp_path, h, _all_bound(h, basis={a: forged for a, _, _ in VOTERS})))


def test_factory_descriptor_resealed_without_canonical_binding(histories, tmp_path):
    h = histories["legit"]
    events, bindings, path = _descriptor_route(h, tmp_path)
    body = json.loads(path.read_bytes())
    # Resealed file: the basis (and so every vote commitment) is unchanged, but the packaged refs are rewritten;
    # only the canonical binding of the descriptor bytes in the ledger can refuse it.
    body["approval_event_sha256"] = body["approval_event_sha256"][:2] + ["e" * 64]
    path.write_bytes(_canonical(body))
    out = _run(tmp_path, h, events, request_extra={"origin_binding_event_sha256": corpus.event_sha256(bindings[-1])})
    _assert_refused("descriptor_resealed_without_canonical_binding", out)


def test_factory_descriptor_binding_event_not_in_ledger(histories, tmp_path):
    h = histories["legit"]
    events, bindings, _ = _descriptor_route(h, tmp_path)
    ledger = [e for e in events if e not in bindings]
    out = _run(tmp_path, h, ledger, request_extra={"origin_binding_event_sha256": corpus.event_sha256(bindings[-1])})
    _assert_refused("descriptor_binding_event_not_in_ledger", out)


def _packaged(tmp_path, h, case, **route):
    events, bindings, _ = _descriptor_route(h, tmp_path, **route)
    out = _run(tmp_path, h, events, request_extra={"origin_binding_event_sha256": corpus.event_sha256(bindings[0])})
    _assert_refused(case, out)


def test_factory_single_voter_attests_another(histories, tmp_path):
    """Only the issuer committed; it packages both peers' uncommitted votes."""
    _packaged(tmp_path, histories["legit"], "single_voter_attests_another", committed=("codex-lead-1",))


def test_factory_proxy_missing_peer_commitment(histories, tmp_path):
    """Lead and RCO committed; the issuer packages the tools vote, which carries no commitment of its own."""
    _packaged(tmp_path, histories["legit"], "proxy_missing_peer_commitment",
              committed=("codex-lead-1", "claude-rco-1"))


def test_factory_proxy_missing_rco_commitment(histories, tmp_path):
    _packaged(tmp_path, histories["legit"], "proxy_missing_rco_commitment",
              committed=("codex-lead-1", "codex-tools-1"))


def test_factory_peer_commitment_differs(histories, tmp_path):
    """Every vote commits, but the RCO committed to another basis digest than the packaged preimage."""
    h = histories["legit"]
    other = _sha(_mismatch(h, "diff_sha256", "2" * 64))
    _packaged(tmp_path, h, "peer_commitment_differs", commit_digest={"claude-rco-1": other})


def test_factory_descriptor_preimage_not_matching_commitments(histories, tmp_path):
    """File and packaging event agree with each other, yet the preimage is not what the votes committed to."""
    h = histories["legit"]
    forged = _sha(_mismatch(h, "patch_id", "3" * 40))
    _packaged(tmp_path, h, "descriptor_preimage_not_matching_commitments",
              commit_digest={a: forged for a in ALL_VOTERS})


def test_factory_request_base_assertion_contradicts_basis(histories, tmp_path):
    h = histories["legit"]
    out = _run(tmp_path, h, _all_bound(h), request_extra={"approved_base_sha": h.live_base})
    _assert_refused("request_base_assertion_contradicts_basis", out)


def test_factory_side_base_substitution_live_base(histories, tmp_path):
    """GitHub reports the real main as the base; against it the new head also brings the unreviewed side commit."""
    h = histories["side"]
    _assert_refused("side_base_substitution_live_base", _run(tmp_path, h, _all_bound(h)))


def test_factory_side_base_substitution_expected_base(histories, tmp_path):
    """The caller names the side commit as the expected base; the live PR base is the real main."""
    h = histories["side"]
    _assert_refused("side_base_substitution_expected_base",
                    _run(tmp_path, h, _all_bound(h), expected_base=h.side_base))


def test_factory_stacked_ancestry_rebased(histories, tmp_path):
    h = histories["stacked_rebased"]
    _assert_refused("stacked_ancestry_rebased", _run(tmp_path, h, _all_bound(h)))


def test_factory_stacked_ancestry_merge_base_assertion(histories, tmp_path):
    """The withdrawn derivation (current-target merge-base F) offered as the request assertion must not be adopted:
    the recorded historical basis names M0."""
    h = histories["stacked_rebased"]
    out = _run(tmp_path, h, _all_bound(h), request_extra={"approved_base_sha": h.feature})
    _assert_refused("stacked_ancestry_merge_base_assertion", out)


def test_factory_stacked_ancestry_no_rewrite(histories, tmp_path):
    h = histories["stacked_no_rewrite"]
    assert h.old == h.new
    _assert_refused("stacked_ancestry_no_rewrite", _run(tmp_path, h, _all_bound(h)))


def test_factory_strict_ci_failed_at_proposed_head(histories, tmp_path):
    h = histories["legit"]
    gh = _gh(h)
    ci_harness._setup("failed_required_check", gh)
    _assert_refused("strict_ci_failed_at_proposed_head", _run(tmp_path, h, _all_bound(h), gh=gh))
