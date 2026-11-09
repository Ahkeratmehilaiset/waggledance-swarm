"""Independent targeted corpus for RCO1 interim F1 (Lead request 2026-11-09T10:39:08Z).

Stacked F+P history reviewed against M0; F lands on main; the attacker re-seals a P-only receipt and matching caller
evidence on the current merge-base F. The published evidence-only helpers are exercised directly (helper misuse) and
separately the production route (live factory and gate) is exercised to show what is reachable. Also: strict PR number
type (bool vs int, PR number 1), the trusted origin descriptor packaged by each original voter, and queue, CI and a
later objection re-checked at use. The MAGMA semantic consumer is not published at the Tools head (RED missing API).
Cases and intended verdicts: tests/fixtures/bridge_consensus_carry_forward/origin_forgery_cases.json. No live GitHub
call; no Tools source is touched.
"""
from __future__ import annotations

import ast
import copy
import importlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "origin_forgery_cases.json")
                   .read_text(encoding="utf-8"))
_SPEC = importlib.util.spec_from_file_location(
    "fable5_origin_forgery_origin", ROOT / "tests" / "tools" / "test_consensus_carry_forward_origin_factory.py")
of = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(of)
histories = of.histories          # real git histories incl. stacked_rebased (module scope)
anchored_fleet = of.anchored_fleet  # autouse: test-owned pinned git generation

from tools.bridge_approval_carry_forward import (  # noqa: E402
    evaluate_approval_carry_forward, verify_carry_forward_receipt,
)
from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate  # noqa: E402
from tools.pr_status_snapshot import build_pr_status_snapshot  # noqa: E402

SEMANTIC_CONSUMER_CANDIDATES = (
    ("tools.bridge_approval_carry_forward", "consume_carry_forward_receipt"),
    ("tools.bridge_approval_carry_forward", "verify_carry_forward_semantic_receipt"),
    ("tools.write_bridge_consensus_merge_receipt", "verify_carry_forward_magma_receipt"),
    # names announced by Tools for the unpushed head a032f622 (handoff 10:42:10Z); none exists at be79431e
    ("tools.bridge_approval_carry_forward", "verify_live_carry_forward_receipt"),
    ("tools.bridge_approval_carry_forward", "verify_carry_forward_merge_receipt"),
    ("tools.write_bridge_consensus_merge_receipt", "verify_carry_forward_merge_receipt"),
)


def _spec(case):
    return CASES["cases"][case]


def _legit_carry(tmp_path, h, events=None):
    out = of._run(tmp_path, h, events or of._all_bound(h))
    assert out.carry is True, out.text
    return out.result["receipt"], out.result["_evidence"]


def _forged_stacked_evidence(template, s, *, approved_base):
    """Caller evidence for the stacked PR: the original votes (bound to the F+P basis on M0), live CI at the
    proposed head, and an approved base chosen by the caller."""
    e = copy.deepcopy(template)
    side = dict(e["approved"])
    e["approved"] = dict(side, head=s.old, base_sha=approved_base)
    e["proposed"] = dict(side, head=s.new, base_sha=s.live_base)
    e["events"] = of._all_bound(s)
    e["repo_path"] = str(Path(s.repo.path))
    for run in e["ci"]["runs"]:
        run["head"] = s.new
    return e


@pytest.fixture
def stacked_pair(histories, tmp_path):
    legit, s = histories["legit"], histories["stacked_rebased"]
    _, template = _legit_carry(tmp_path, legit)
    assert s.repo.merge_bases(s.live_base, s.old) == [s.feature], "precondition: F is the current merge-base"
    assert s.old_base != s.feature, "precondition: the reviewed base M0 differs from F"
    return s, template


# ----------------------------------------------------------------------------------------------------------------
# helper misuse vs production reachability
# ----------------------------------------------------------------------------------------------------------------
def test_helper_forged_stacked_receipt_and_evidence(stacked_pair):
    s, template = stacked_pair
    evidence = _forged_stacked_evidence(template, s, approved_base=s.feature)
    forged = evaluate_approval_carry_forward(copy.deepcopy(evidence))
    if forged["carry"] is not True:
        assert verify_carry_forward_receipt(forged.get("receipt") or {}, evidence) is False
        return  # the helper itself refuses: no helper gap
    receipt = forged["receipt"]
    assert receipt["approved_base_sha"] == s.feature and receipt["approved_diff_sha256"] == receipt["proposed_diff_sha256"]
    accepted = verify_carry_forward_receipt(copy.deepcopy(receipt), copy.deepcopy(evidence))
    if accepted is not False:
        pytest.fail("RED: helper gap (not production reachable at the Tools head, see production_* tests): the "
                    "evidence-only verifier accepts a P-only receipt resealed on the current merge-base F although "
                    "every original vote is bound to the F+P reviewed basis on M0; a semantic consumer must re-derive "
                    "the approved base from the votes' bound basis, never from caller evidence")


def test_helper_honest_stacked_evidence(stacked_pair):
    s, template = stacked_pair
    evidence = _forged_stacked_evidence(template, s, approved_base=s.old_base)
    honest = evaluate_approval_carry_forward(copy.deepcopy(evidence))
    assert honest["carry"] is False and re.search("(?i)content|diff|identical", honest["reason"]), honest["reason"]


def test_production_factory_stacked(histories, tmp_path):
    s = histories["stacked_rebased"]
    out = of._run(tmp_path, s, of._all_bound(s))
    assert out.carry is False, out.text
    assert of._merge_calls(out.gh) == []


def test_production_factory_stacked_base_hint_current_merge_base(histories, tmp_path):
    s = histories["stacked_rebased"]
    out = of._run(tmp_path, s, of._all_bound(s), request_extra={"approved_base_sha": s.feature})
    assert out.carry is False, out.text
    assert re.search("(?i)base|basis", out.text), out.text


def _snapshot(h, gh):
    return build_pr_status_snapshot(pr_number=of.PR, repo=of.REPO_NAME, receipt_verified=True,
                                    expected_base_sha=h.live_base, runner=gh, require_ci_evidence=True)


def _gate(tmp_path, h, events, *, gh=None, pr_status=None, request=None):
    gh = gh or of._gh(h)
    pr_status = pr_status if pr_status is not None else _snapshot(h, gh)
    return evaluate_auto_merge_gate(
        pr_status=pr_status, expected_head=h.new, expected_base_sha=h.live_base, consensus_proposal_id=of.TASK,
        receipt_bundle_path="docs/receipts/manifest.json", events_path=of._events_file(tmp_path, events),
        bridge_task_id=of.TASK, repo=of.REPO_NAME, from_agent="codex-lead-1", require_bridge_consensus=True,
        accepted_queue_checker=of._queue(), runner=gh,
        carry_forward=request or {"approved_head": h.old, "repo_root": Path(h.repo.path)}), gh


def test_production_gate_plan_stacked(histories, tmp_path):
    s = histories["stacked_rebased"]
    report, gh = _gate(tmp_path, s, of._all_bound(s),
                       request={"approved_head": s.old, "repo_root": Path(s.repo.path), "approved_base_sha": s.feature})
    assert report.get("ok") is not True and report.get("decision") == "operator_review_required", report
    assert any(r.startswith("carry refused") for r in report.get("reasons", [])), report
    assert of._merge_calls(gh) == []


def test_production_descriptor_p_only_basis_by_lead(histories, tmp_path):
    s = histories["stacked_rebased"]
    approvals = of._approvals(s, commit=of.ALL_VOTERS)  # every vote commits to the F+P basis on M0
    p_only = of._basis(s, base=s.feature)
    body = {"schema": of.SNAPSHOT_SCHEMA, "review_basis": p_only, "review_basis_sha256": of._sha(p_only),
            "approval_event_sha256": [of.corpus.event_sha256(e) for e in approvals]}
    raw = of._canonical(body)
    path = Path(s.repo.path).resolve() / ".codex-audit" / "review-origin" / tmp_path.name / "p_only.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    descriptor = {"path": str(path), "bytes": len(raw), "sha256": of.hashlib.sha256(raw).hexdigest()}
    binding = of._binding(s, "codex-lead-1", descriptor, p_only, body["approval_event_sha256"], 10)
    out = of._run(tmp_path, s, [of._claim()] + approvals + [binding],
                  request_extra={"origin_binding_event_sha256": of.corpus.event_sha256(binding)})
    assert out.carry is False, out.text
    assert re.search("(?i)basis|commit|origin|preimage|digest", out.text), out.text


def test_production_callers_map():
    """Every production call of the evidence-only helpers, pinned; a new caller must be reviewed for F1."""
    names = {"evaluate_approval_carry_forward", "verify_carry_forward_receipt", "derive_live_carry_forward",
             "verify_live_carry_forward_receipt"}
    found = set()
    for base in ("tools", "waggledance", "sandbox", "ops"):
        for path in sorted((ROOT / base).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                    if name in names:
                        found.add((path.relative_to(ROOT).as_posix(), name))
    reviewed = {(row["path"], row["call"]) for row in CASES["reviewed_production_calls"]}
    assert sorted(found - reviewed) == [], "unreviewed production call of a carry helper (review it for F1)"
    gate = {("tools/idle_consensus_auto_merge.py", "derive_live_carry_forward"),
            ("tools/idle_consensus_auto_merge.py", "verify_carry_forward_receipt")}
    assert gate <= found, sorted(found)


# ----------------------------------------------------------------------------------------------------------------
# strict PR number type: PR number 1, where True == 1 in Python
# ----------------------------------------------------------------------------------------------------------------
@pytest.fixture
def pr_one(monkeypatch):
    monkeypatch.setattr(of, "PR", 1)
    monkeypatch.setitem(of._basis.__kwdefaults__, "pr", 1)
    return 1


def test_pr_bool_in_receipt(histories, tmp_path, pr_one):
    receipt, evidence = _legit_carry(tmp_path, histories["legit"])
    assert receipt["pr_number"] == 1 and type(receipt["pr_number"]) is int
    receipt = dict(receipt, pr_number=True)
    if verify_carry_forward_receipt(receipt, copy.deepcopy(evidence)) is not False:
        pytest.fail("actual defect: verify_carry_forward_receipt accepts pr_number true for PR 1 (bool alias via "
                    "dict equality)")


def test_pr_bool_in_evidence(histories, tmp_path, pr_one):
    receipt, evidence = _legit_carry(tmp_path, histories["legit"])
    evidence = copy.deepcopy(evidence)
    evidence["approved"]["pr_number"] = evidence["proposed"]["pr_number"] = True
    assert evaluate_approval_carry_forward(copy.deepcopy(evidence))["carry"] is False
    assert verify_carry_forward_receipt(copy.deepcopy(receipt), evidence) is False


def test_pr_bool_factory_argument(histories, tmp_path, pr_one):
    h = histories["legit"]
    try:
        result = of._factory()(request={"approved_head": h.old, "repo_root": Path(h.repo.path)},
                               events_path=of._events_file(tmp_path, of._all_bound(h)), repo=of.REPO_NAME,
                               pr_number=True, task_id=of.TASK, expected_head=h.new, expected_base_sha=h.live_base,
                               runner=of._gh(h), accepted_queue_checker=of._queue())
    except (ValueError, TypeError, of.ci_harness.PrStatusSnapshotError):
        return
    if result.get("carry") is not False:
        pytest.fail("actual defect: derive_live_carry_forward carries with pr_number=True for PR 1")


def test_pr_bool_gate_snapshot(histories, tmp_path, pr_one):
    h = histories["legit"]
    gh = of._gh(h)
    status = dict(_snapshot(h, gh), pr_number=True)
    try:
        report, _ = _gate(tmp_path, h, of._all_bound(h), gh=gh, pr_status=status)
    except Exception as exc:  # the gate's own invalid-input error is a refusal
        assert "pr_number" in str(exc) or "integer" in str(exc), exc
        return
    assert report.get("ok") is not True, report


def test_pr_bool_in_vote_payload(histories, tmp_path, pr_one):
    h = histories["legit"]
    events = of._all_bound(h)
    for event in events[1:]:
        event["payload"]["pr"] = True
    out = of._run(tmp_path, h, events)
    if out.carry is not False:
        pytest.fail("actual defect: original votes naming the PR as JSON true are carried for PR 1 (bool alias in "
                    "the vote PR binding)")


# ----------------------------------------------------------------------------------------------------------------
# trusted origin descriptor packaged by each original voter and by non-voters
# ----------------------------------------------------------------------------------------------------------------
ISSUERS = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]


@pytest.mark.parametrize("issuer", ISSUERS)
def test_descriptor_issuer(histories, tmp_path, issuer):
    h = histories["legit"]
    events, bindings, _ = of._descriptor_route(h, tmp_path, issuer=issuer)
    out = of._run(tmp_path, h, events,
                  request_extra={"origin_binding_event_sha256": of.corpus.event_sha256(bindings[-1])})
    expect = _spec("descriptor_issuer_" + issuer)["expect"]
    assert out.carry is (expect == "carry"), out.text
    assert of._merge_calls(out.gh) == []


# ----------------------------------------------------------------------------------------------------------------
# queue, CI and a later objection re-checked at use
# ----------------------------------------------------------------------------------------------------------------
def test_at_use_queue_incomplete(histories, tmp_path):
    receipt, evidence = _legit_carry(tmp_path, histories["legit"])
    assert verify_carry_forward_receipt(receipt, dict(copy.deepcopy(evidence), accepted_queue_complete=False)) is False


def test_at_use_ci_failed_at_proposed_head(histories, tmp_path):
    receipt, evidence = _legit_carry(tmp_path, histories["legit"])
    evidence = copy.deepcopy(evidence)
    evidence["ci"]["runs"][0]["conclusion"] = "failure"
    assert verify_carry_forward_receipt(receipt, evidence) is False


def test_at_use_later_rco_change_request(histories, tmp_path):
    h = histories["legit"]
    receipt, evidence = _legit_carry(tmp_path, h)
    evidence = copy.deepcopy(evidence)
    evidence["events"] = evidence["events"] + [of._event("claude-rco-1", "decision", "changes_requested", h.old, 40)]
    assert verify_carry_forward_receipt(receipt, evidence) is False


def test_at_use_gate_ledger_with_later_rco_change_request(histories, tmp_path):
    h = histories["legit"]
    events = of._all_bound(h) + [of._event("claude-rco-1", "decision", "changes_requested", h.old, 40)]
    report, gh = _gate(tmp_path, h, events)
    assert report.get("ok") is not True, report
    assert any(r.startswith("carry refused") for r in report.get("reasons", [])), report
    assert of._merge_calls(gh) == []


# ----------------------------------------------------------------------------------------------------------------
# the actual MAGMA semantic consumer
# ----------------------------------------------------------------------------------------------------------------
def _semantic_consumer():
    module = importlib.import_module("tools.bridge_approval_carry_forward")
    fn = getattr(module, "verify_live_carry_forward_receipt", None)
    if callable(fn):
        return fn
    published = [f"{m}:{a}" for m, a in SEMANTIC_CONSUMER_CANDIDATES
                 if callable(getattr(importlib.import_module(m), a, None))]
    if published:
        pytest.fail(f"ADAPT: semantic consumer published as {published}; drive the forged stacked material through it")
    pytest.fail(f"RED: missing API: no MAGMA semantic carry consumer is published at this head (candidates in "
                f"SEMANTIC_CONSUMER_CANDIDATES; none at {CASES['tools_head']})")


def _consume(consumer, tmp_path, h, material, events, request=None, runner=None):
    return consumer(material=material, request=request or {"approved_head": h.old, "repo_root": Path(h.repo.path)},
                    events_path=of._events_file(tmp_path, events), repo=of.REPO_NAME, pr_number=of.PR,
                    task_id=of.TASK, expected_head=h.new, expected_base_sha=h.live_base,
                    runner=of._gh(h) if runner is None else runner,
                    accepted_queue_checker=of._queue())


def _gh_effects(commands):
    """Flag non-GET/merge or unrecognized synthetic gh calls, fail closed.

    Models the installed `gh api --help` method/parameter rules. It classifies
    the recorded command, not arbitrary server-side semantics or shell text.
    """
    effects = []
    value_options = {'--hostname', '--header', '-H', '--jq', '-q', '--template', '-t',
                     '--cache', '--preview', '-p'}
    switches = {'--paginate', '--slurp', '--silent', '--include', '-i', '--verbose'}
    parameters = {'-f', '-F', '--field', '--raw-field', '--input'}
    for command in commands:
        if not isinstance(command, (list, tuple)) or not command or not all(type(arg) is str for arg in command):
            effects.append(command)
            continue
        call = list(command)
        if call[:3] in (['gh', 'pr', 'view'], ['gh', 'pr', 'diff']):
            continue
        if call[:2] != ['gh', 'api']:
            effects.append(command)
            continue
        methods, positional = [], []
        has_parameters, invalid = False, False
        index = 2
        while index < len(call):
            option = call[index]
            if option in {'-X', '--method'} | parameters | value_options:
                if index + 1 >= len(call):
                    invalid = True
                    break
                value = call[index + 1]
                index += 2
                if option in {'-X', '--method'}:
                    methods.append(value.upper())
                elif option in parameters:
                    has_parameters = True
                continue
            if option.startswith('--method='):
                methods.append(option.split('=', 1)[1].upper())
            elif option.startswith('-X'):
                methods.append(option[2:].removeprefix('=').upper())
            elif any(option.startswith(name + '=') for name in parameters | value_options if name.startswith('--')):
                has_parameters |= any(option.startswith(name + '=') for name in parameters)
            elif any(option.startswith(name) and len(option) > 2 for name in ('-f', '-F')):
                has_parameters = True
            elif any(option.startswith(name) and len(option) > 2 for name in ('-H', '-q', '-t', '-p')):
                pass
            elif option in switches:
                pass
            elif option.startswith('-'):
                invalid = True
            else:
                positional.append(option)
            index += 1
        # Multiple methods and unknown options do not prove a read, even if a
        # particular CLI version might accept or reject the ambiguous command.
        if (invalid or len(positional) != 1 or len(methods) > 1
                or (methods[0] if methods else ('POST' if has_parameters else 'GET')) != 'GET'):
            effects.append(command)
    return effects


def test_semantic_consumer_rederives_reviewed_basis(histories, tmp_path, stacked_pair):
    """Published semantic consumer (verify_live_carry_forward_receipt at a032f622): a legit material verifies; the
    forged P-only stacked material (receipt, a P-only review origin and live CI) is refused because the consumer
    re-derives the approved base from the votes' bound F+P basis instead of the caller's material."""
    consumer = _semantic_consumer()
    legit = histories["legit"]
    out = of._run(tmp_path, legit, of._all_bound(legit))
    assert out.carry is True, out.text
    material = {"schema": "wd.bridge-carry-forward-merge-proof.v1", "approval_receipt": out.result["receipt"],
                "review_origin": out.result["review_origin"], "ci_evidence": out.result["ci_evidence"]}
    (tmp_path / "control").mkdir()
    control_runner = of._gh(legit)
    control = _consume(consumer, tmp_path / "control", legit, copy.deepcopy(material), of._all_bound(legit),
                       runner=control_runner)
    assert control.get("ok") is True, control
    assert of._merge_calls(control_runner) == []
    assert not _gh_effects(control_runner.calls)
    s, template = stacked_pair
    evidence = _forged_stacked_evidence(template, s, approved_base=s.feature)
    # Explicitly untrusted diagnostic construction: omit expanded voter bases
    # only in this scratch input, then restore the real event commitments. The
    # unchanged helper-refusal probe above still supplies the bound attack.
    unbound = copy.deepcopy(evidence)
    for event in unbound["events"]:
        event.get("payload", {}).pop("review_basis", None)
        event.get("payload", {}).pop("review_basis_sha256", None)
    diagnostic = evaluate_approval_carry_forward(unbound)
    assert diagnostic["carry"] is True and diagnostic["authority"] == "evidence_only", diagnostic
    untrusted_receipt = diagnostic["receipt"]
    from tools.bridge_approval_carry_forward import _canonical_sha
    refs = {_canonical_sha(before): _canonical_sha(after)
            for before, after in zip(unbound["events"], evidence["events"])}
    untrusted_receipt["approval_event_sha256"] = [refs[ref] for ref in untrusted_receipt["approval_event_sha256"]]
    for row in untrusted_receipt["carried"]:
        row["event_sha256"] = refs[row["event_sha256"]]
    untrusted_receipt["accepted_events_sha256"] = _canonical_sha(evidence["events"])
    untrusted_receipt["original_approval_basis_sha256"] = _canonical_sha(dict(
        carried=untrusted_receipt["carried"],
        build_author_slot_waivers=untrusted_receipt["build_author_slot_waivers"]))
    assert untrusted_receipt["approved_base_sha"] == s.feature != s.old_base
    assert s.repo.merge_bases(s.live_base, s.old) == [s.feature]
    assert untrusted_receipt["approved_diff_sha256"] == untrusted_receipt["proposed_diff_sha256"]
    assert untrusted_receipt["content_identity"]["old_patch_id"] == untrusted_receipt["patch_id"]
    p_only = of._basis(s, base=s.feature)
    forged_material = dict(material, approval_receipt=untrusted_receipt,
                           review_origin=dict(material["review_origin"], review_basis=p_only))
    (tmp_path / "forged").mkdir()
    for request in ({"approved_head": s.old, "repo_root": Path(s.repo.path)},
                    {"approved_head": s.old, "repo_root": Path(s.repo.path), "approved_base_sha": s.feature}):
        attack_runner = of._gh(s)
        result = _consume(consumer, tmp_path / "forged", s, copy.deepcopy(forged_material), of._all_bound(s),
                          request=request, runner=attack_runner)
        assert result.get("ok") is not True, result
        reason = "; ".join(str(item) for item in (result.get("reasons") or result.get("errors") or []))
        assert re.search(r"base|basis|diff bytes", reason, re.I), result
        assert not re.search(r"malformed|schema|parse|seal", reason, re.I), result
        assert of._merge_calls(attack_runner) == []
        assert not _gh_effects(attack_runner.calls)
    # the published consumer also refuses a legit material whose receipt was resealed on a type alias
    aliased = dict(material, approval_receipt=dict(material["approval_receipt"],
                                                   pr_number=float(material["approval_receipt"]["pr_number"])))
    (tmp_path / "alias").mkdir()
    alias_runner = of._gh(legit)
    assert _consume(consumer, tmp_path / "alias", legit, aliased, of._all_bound(legit),
                    runner=alias_runner).get("ok") is not True
    assert of._merge_calls(alias_runner) == []
    assert not _gh_effects(alias_runner.calls)


def test_every_fixture_case_has_a_test():
    tests = {name[len("test_"):] for name in globals() if name.startswith("test_")}
    tests |= {"descriptor_issuer_" + issuer for issuer in ISSUERS}
    assert sorted(set(CASES["cases"]) - tests) == []
