"""Independent adversarial carry receipt cases (fable-5, Lead request 2026-11-09T10:16:25Z).

The writer is the published live factory (derive_live_carry_forward) on the real git histories and the synthetic
strict GitHub of the origin-factory suite; the consumer is the published verify_carry_forward_receipt against the
current ledger, CI and queue observation. Every case names its intended verdict in
tests/fixtures/bridge_consensus_carry_forward/receipt_adversary_cases.json. Unpublished interfaces (merge carry input,
MAGMA writer carry input) stay RED missing API; the published gate apply path is asserted to refuse.
Second route (Lead request 2026-11-09T10:53:35Z, Tools head a032f622): the same cases with their intended verdicts
driven through the actual MAGMA writer (write_bridge_consensus_merge_receipt carry_forward input) and the live
semantic consumer (verify_carry_forward_merge_receipt); the fixture section magma_route records each route and
every invocation adaptation. Merge apply carry input stays separately RED missing API.
No live GitHub call; no Tools source is touched.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "receipt_adversary_cases.json")
                   .read_text(encoding="utf-8"))
_SPEC = importlib.util.spec_from_file_location(
    "fable5_receipt_adversary_origin", ROOT / "tests" / "tools" / "test_consensus_carry_forward_origin_factory.py")
of = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(of)
histories = of.histories          # real git histories (module scope)
anchored_fleet = of.anchored_fleet  # autouse: test-owned pinned git generation

from tools.bridge_approval_carry_forward import evaluate_approval_carry_forward, verify_carry_forward_receipt  # noqa: E402,E501
from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate  # noqa: E402
from tools.merge_with_bridge_receipt import merge_with_bridge_receipt  # noqa: E402
from tools.pr_status_snapshot import build_pr_status_snapshot  # noqa: E402
from tools.write_bridge_consensus_merge_receipt import write_bridge_consensus_merge_receipt  # noqa: E402

CARRY_PARAMETERS = ("carry_forward", "approval_carry_forward", "carry_receipt")


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                     allow_nan=False).encode("ascii")).hexdigest()


def _carry(tmp_path, h, events):
    out = of._run(tmp_path, h, events)
    assert out.carry is True, out.text
    receipt, evidence = out.result["receipt"], out.result["_evidence"]
    assert verify_carry_forward_receipt(copy.deepcopy(receipt), copy.deepcopy(evidence)) is True
    return SimpleNamespace(h=h, receipt=receipt, evidence=evidence, out=out)


@pytest.fixture
def carry(histories, tmp_path):
    h = histories["legit"]
    return _carry(tmp_path, h, of._all_bound(h))


def _reseal(r):
    """Recompute every receipt-internal digest so a forged receipt is self-consistent."""
    r["approval_event_sha256"] = [row["event_sha256"] for row in r["carried"]]
    r["original_approval_basis_sha256"] = _sha({"carried": r["carried"],
                                                "build_author_slot_waivers": r["build_author_slot_waivers"]})
    identity = r["content_identity"]
    r["approved_diff_sha256"], r["proposed_diff_sha256"] = identity["old_diff_sha256"], identity["new_diff_sha256"]
    r["patch_id"] = identity["old_patch_id"]
    r["approved_merge_base"], r["proposed_merge_base"] = identity["old_merge_base"], identity["new_merge_base"]
    r["diff_argv"] = identity["diff_argv"]
    return r


def _ev(agent, typ, status, head, minute, **kw):
    return of._event(agent, typ, status, head, minute, **kw)


# ----------------------------------------------------------------------------------------------------------------
# Mutations: each returns (receipt, evidence) built from the valid pair c; the fixture names the verdict.
# ----------------------------------------------------------------------------------------------------------------
def _m_control_valid(c, r, e):
    return r, e


def _m_control_reseal_is_faithful(c, r, e):
    resealed = _reseal(copy.deepcopy(r))
    assert resealed == r, "the reseal helper must reproduce an untouched receipt exactly"
    return resealed, e


def _m_control_json_roundtrip(c, r, e):
    return json.loads(json.dumps(r)), json.loads(json.dumps(e))


def _m_control_key_order(c, r, e):
    return dict(reversed(list(r.items()))), e


def _role_row(r, agent):
    return next(row for row in r["carried"] if row["agent"] == agent)


def _m_resealed_rco_identity_swapped(c, r, e):
    row = _role_row(r, "claude-rco-1")
    row["agent"], row["agent_uuid"] = "claude-rco-3", of.REGISTRY.get("claude-rco-3", row["agent_uuid"])
    return _reseal(r), e


def _m_resealed_event_digest_to_claim(c, r, e):
    _role_row(r, "codex-tools-1")["event_sha256"] = _sha(e["events"][0])
    return _reseal(r), e


def _m_resealed_veto_controls_dropped(c, r, e):
    assert r["veto_controls"], "precondition: the valid receipt records scoped controls"
    r["veto_controls"] = []
    return _reseal(r), e


def _m_resealed_content_identity_digests(c, r, e):
    r["content_identity"]["old_diff_sha256"] = r["content_identity"]["new_diff_sha256"] = "e" * 64
    return _reseal(r), e


def _m_resealed_proposed_head(c, r, e):
    other = c.h.old_base
    r["proposed_head"] = r["ci_head"] = other
    for row in r["ci_checks"]:
        row["head"] = other
    return _reseal(r), e


def _m_resealed_approved_base_to_live_base(c, r, e):
    assert r["approved_base_sha"] != c.h.live_base
    r["approved_base_sha"] = c.h.live_base
    return _reseal(r), e


def _m_resealed_task_id(c, r, e):
    r["task_id"] = of.TOOLS_TASK
    return _reseal(r), e


def _m_resealed_registry_digest(c, r, e):
    r["registry_sha256"] = _sha({})
    return _reseal(r), e


def _m_resealed_forged_author_waiver(c, r, e):
    r["carried"] = [row for row in r["carried"] if row["agent"] != "codex-lead-1"]
    r["build_author_slot_waivers"] = list(r["build_author_slot_waivers"]) + [
        {"role": "build_lead", "agent": "codex-lead-1", "waived": True}]
    return _reseal(r), e


def _m_resealed_ci_checks_from_approved_head(c, r, e):
    r["ci_head"] = c.h.old
    for row in r["ci_checks"]:
        row["head"] = c.h.old
    return _reseal(r), e


def _m_extra_top_level_merge_authorization(c, r, e):
    r["merge_authorized"] = True
    return r, e


def _m_extra_operator_signature(c, r, e):
    r["operator_signature"] = {"signed_by": "operator", "scope": "carry"}
    return r, e


def _m_extra_carried_row(c, r, e):
    row = dict(_role_row(r, "claude-rco-1"), agent="claude-rco-3")
    r["carried"] = list(r["carried"]) + [row]
    return _reseal(r), e


def _m_extra_field_in_carried_row(c, r, e):
    _role_row(r, "codex-lead-1")["authority"] = "merge"
    return _reseal(r), e


def _m_evidence_rco_subset_hides_later_veto(c, r, e):
    e["recognized_rco_agents"] = ["claude-rco-2"]
    e["events"] = e["events"] + [_ev("claude-rco-1", "decision", "changes_requested", c.h.old, 40)]
    return r, e


def _m_alias_pr_number_float(c, r, e):
    r["pr_number"] = float(r["pr_number"])
    return r, e


def _m_alias_pr_number_string(c, r, e):
    r["pr_number"] = str(r["pr_number"])
    return r, e


def _m_alias_veto_scan_complete_one(c, r, e):
    r["veto_scan_complete"] = 1
    return r, e


def _m_alias_control_index_float(c, r, e):
    r["veto_controls"][0]["index"] = float(r["veto_controls"][0]["index"])
    return r, e


def _m_alias_event_count_bool(c, r, e):
    assert r["accepted_event_count"] != 1
    r["accepted_event_count"] = True
    return r, e


def _m_alias_event_count_float(c, r, e):
    r["accepted_event_count"] = float(r["accepted_event_count"])
    return r, e


def _m_alias_ci_conclusion_case(c, r, e):
    r["ci_checks"][0]["conclusion"] = "SUCCESS"
    return r, e


def _m_alias_schema_trailing_space(c, r, e):
    r["schema"] = r["schema"] + " "
    return r, e


def _append(e, *events):
    e["events"] = e["events"] + list(events)
    return e


def _m_append_harmless_rco_diagnostic(c, r, e):
    return r, _append(e, _ev("claude-rco-1", "message", "info", c.h.old, 41, payload={"note": "diagnostic only"}))


def _m_append_harmless_other_task_note(c, r, e):
    return r, _append(e, _ev("claude-rco-2", "message", "info", None, 42, task="claude-rco-2/unrelated-note"))


def _m_append_rco_changes_requested_old_head(c, r, e):
    return r, _append(e, _ev("claude-rco-1", "decision", "changes_requested", c.h.old, 43))


def _m_append_rco_typed_block_new_head(c, r, e):
    return r, _append(e, _ev("claude-rco-1", "finding", "open", c.h.new, 44))


def _m_append_rco_veto_descriptive_task_head_bound(c, r, e):
    return r, _append(e, _ev("claude-rco-1", "decision", "changes_requested", c.h.old, 45,
                             task="claude-rco-1/descriptive-review-of-carry"))


def _m_append_build_lead_block(c, r, e):
    return r, _append(e, _ev("codex-lead-1", "decision", "blocked", c.h.old, 46))


def _m_append_rco_pass_after_own_veto(c, r, e):
    return r, _append(e, _ev("claude-rco-1", "decision", "changes_requested", c.h.old, 47),
                      _ev("claude-rco-1", "decision", "rco_pass", c.h.old, 48))


def _m_append_rco_bound_pass_after_own_veto(c, r, e):
    basis = of._basis(c.h)
    return r, _append(e, _ev("claude-rco-1", "decision", "changes_requested", c.h.old, 51),
                      _ev("claude-rco-1", "decision", "rco_pass", c.h.old, 52,
                          payload={"review_basis": basis, "review_basis_sha256": of._sha(basis),
                                   "exact_head": c.h.old}))


def _m_stale_queue_incomplete(c, r, e):
    e["accepted_queue_complete"] = False
    return r, e


def _m_stale_ci_failed_at_proposed_head(c, r, e):
    e["ci"]["runs"][-1]["conclusion"] = "failure"
    return r, e


def _m_stale_ci_pending_at_proposed_head(c, r, e):
    e["ci"]["runs"][-1].update(status="in_progress", conclusion="")
    return r, e


def _m_stale_ci_runs_at_approved_head(c, r, e):
    for run in e["ci"]["runs"]:
        run["head"] = c.h.old
    return r, e


def _m_stale_ci_required_set_grew(c, r, e):
    e["ci"]["required"] = list(e["ci"]["required"]) + [json.dumps(["late-required-check", 15368],
                                                                  separators=(",", ":"))]
    return r, e


def _prefix(r, e, count):
    r["accepted_event_count"] = count
    r["accepted_events_sha256"] = _sha(e["events"][:count])
    return r, e


def _m_prefix_truncated_excludes_rco_vote(c, r, e):
    rco_index = max(i for i, ev in enumerate(e["events"]) if ev["agent"] == "claude-rco-1")
    return _prefix(r, e, rco_index)


def _m_prefix_count_beyond_ledger(c, r, e):
    r["accepted_event_count"] = len(e["events"]) + 1
    return r, e


def _m_prefix_count_zero(c, r, e):
    r["accepted_event_count"] = 0
    return r, e


def _m_prefix_reordered(c, r, e):
    events = list(e["events"])
    events[1], events[2] = events[2], events[1]
    e["events"] = events
    return r, e


def _m_prefix_event_inserted_inside(c, r, e):
    events = list(e["events"])
    events.insert(1, _ev("claude-rco-2", "message", "info", None, 49, task="claude-rco-2/unrelated-note"))
    e["events"] = events
    return r, e


MUTATIONS = {name[3:]: fn for name, fn in globals().items() if name.startswith("_m_")}
VERIFY_CASES = sorted(name for name, spec in CASES["cases"].items() if spec["class"] not in ("apply", "magma_payload")
                      and name != "prefix_truncated_harmless_tail")


def test_every_fixture_case_has_an_implementation():
    special = {"prefix_truncated_harmless_tail", "apply_gate_refuses_carry_apply", "apply_merge_carry_input",
               "apply_magma_writer_carry_input"}
    magma_only = {n for n, spec in CASES["cases"].items() if spec["class"] == "magma_payload"}
    assert sorted(set(CASES["cases"]) - special - magma_only) == sorted(MUTATIONS)
    assert all(CASES["cases"][name]["class"] in CASES["classes"] for name in CASES["cases"])


@pytest.mark.parametrize("case", VERIFY_CASES)
def test_receipt_consumer(carry, case):
    spec = CASES["cases"][case]
    receipt, evidence = MUTATIONS[case](carry, copy.deepcopy(carry.receipt), copy.deepcopy(carry.evidence))
    current = evaluate_approval_carry_forward(copy.deepcopy(evidence))
    if "reason" in spec:  # the refusal must come from the intended class, not an incidental failure
        assert current["carry"] is False and re.search(spec["reason"], current["reason"]), current["reason"]
    if spec["class"] == "prefix":  # isolation: the evidence itself still carries; only the prefix check may refuse
        assert current["carry"] is True, current["reason"]
    verdict = verify_carry_forward_receipt(receipt, evidence)
    if spec["expect"] == "accept":
        assert verdict is True, case
    elif verdict is not False:
        pytest.fail(f"actual defect: verify_carry_forward_receipt accepts {case} ({spec['class']}); intended verdict "
                    f"refuse")


def test_receipt_prefix_truncated_harmless_tail(histories, tmp_path):
    """Writer run with a harmless tail event; the forged shorter prefix still holds every material event."""
    h = histories["legit"]
    tail = _ev("claude-rco-2", "message", "info", None, 50, task="claude-rco-2/unrelated-note")
    c = _carry(tmp_path, h, of._all_bound(h) + [tail])
    assert c.receipt["accepted_event_count"] == len(c.evidence["events"])
    receipt, evidence = _prefix(copy.deepcopy(c.receipt), copy.deepcopy(c.evidence),
                                c.receipt["accepted_event_count"] - 1)
    verdict = verify_carry_forward_receipt(receipt, evidence)
    assert verdict is (CASES["cases"]["prefix_truncated_harmless_tail"]["expect"] == "accept")


def test_apply_gate_refuses_carry_apply(histories, tmp_path):
    h = histories["legit"]
    gh = of._gh(h)
    snapshot = build_pr_status_snapshot(pr_number=of.PR, repo=of.REPO_NAME, receipt_verified=True,
                                        expected_base_sha=h.live_base, runner=gh, require_ci_evidence=True)
    report = evaluate_auto_merge_gate(
        pr_status=snapshot, expected_head=h.new, expected_base_sha=h.live_base, consensus_proposal_id=of.TASK,
        receipt_bundle_path="docs/receipts/manifest.json", events_path=of._events_file(tmp_path, of._all_bound(h)),
        bridge_task_id=of.TASK, repo=of.REPO_NAME, from_agent="codex-lead-1", require_bridge_consensus=True,
        accepted_queue_checker=of._queue(), runner=gh, apply=True,
        carry_forward={"approved_head": h.old, "repo_root": Path(h.repo.path)})
    assert report.get("ok") is not True and report.get("decision") == "operator_review_required", report
    assert any("carry apply" in reason for reason in report.get("reasons", [])), report
    assert of._merge_calls(gh) == []


@pytest.mark.parametrize("target", [merge_with_bridge_receipt, write_bridge_consensus_merge_receipt],
                         ids=["apply_merge_carry_input", "apply_magma_writer_carry_input"])
def test_apply_unpublished_carry_input(target):
    names = [p for p in CARRY_PARAMETERS if p in inspect.signature(target).parameters]
    if target is write_bridge_consensus_merge_receipt and names == ["carry_forward"]:
        return  # published at a032f622; every verdict case is driven through it by test_magma_writer_and_consumer
    if not names:
        pytest.fail(f"RED: missing API: {target.__module__}.{target.__name__} has no carry input yet (intentionally "
                    f"unavailable at {CASES['tools_head']}; fixture interfaces)")
    pytest.fail(f"ADAPT: {target.__name__} now publishes {names}; drive the resealed, alias, append, stale and prefix "
                "cases through it before trusting it")


# ----------------------------------------------------------------------------------------------------------------
# Actual MAGMA writer + live semantic consumer route (Lead 2026-11-09T10:53:35Z, Tools head a032f622).
# Writer = write_bridge_consensus_merge_receipt(carry_forward=...) on a real git history; consumer =
# verify_carry_forward_merge_receipt (MAGMA files -> verify_live_carry_forward_receipt). Every case keeps the
# intended verdict above; fixture "magma_route" records the route and each invocation adaptation.
# ----------------------------------------------------------------------------------------------------------------
MAGMA = CASES["magma_route"]
MAGMA_PATH = MAGMA["changed_path"]


def _magma_api():
    import tools.write_bridge_consensus_merge_receipt as writer_module
    writer = writer_module.write_bridge_consensus_merge_receipt
    consumer = getattr(writer_module, "verify_carry_forward_merge_receipt", None)
    if "carry_forward" not in inspect.signature(writer).parameters or consumer is None:
        pytest.fail(f"RED: missing API: MAGMA writer carry input or semantic consumer not published (fixture "
                    f"magma_route; Tools head {MAGMA['tools_head']})")
    return writer_module, writer, consumer


@pytest.fixture(scope="module")
def magma_history(tmp_path_factory):
    """Legit rebase on a real git history whose PR path is on the merge path allowlist (adaptation: the
    origin-factory path src/feature.txt is refused by the unchanged writer path gate)."""
    repo = of.corpus.Repo(tmp_path_factory.mktemp("magma") / "repo")
    main0 = repo.commit({MAGMA_PATH: b"base one\n", "docs/readme.txt": b"base\n"}, "base")
    repo.checkout("pr", main0)
    old = repo.commit({MAGMA_PATH: b"base one\nfeature two\n"}, "pr: add feature")
    repo.checkout("main", main0)
    main1 = repo.commit({"docs/notes.txt": b"unrelated main change\n"}, "main advances")
    repo.checkout("pr-rebased", main1)
    new = repo.commit({MAGMA_PATH: b"base one\nfeature two\n"}, "pr: add feature")
    repo.git("remote", "add", "origin", f"https://github.com/{of.REPO_NAME}.git")
    return SimpleNamespace(repo=repo, old=old, old_base=main0, new=new, live_base=main1, feature=None)


class _Magma:
    """One writer/consumer environment: ledger, synthetic strict GitHub and queue observation."""

    def __init__(self, h, tmp):
        tmp.mkdir(parents=True, exist_ok=True)
        self.h, self.tmp, self.n = h, tmp, 0
        self.events = of._all_bound(h)
        self.gh = of._gh(h)
        self.queue = True
        self.request = {"approved_head": h.old, "repo_root": Path(h.repo.path)}

    def live(self):
        return dict(events_path=of._events_file(self.tmp, self.events), repo=of.REPO_NAME,
                    expected_head=self.h.new, expected_base_sha=self.h.live_base, runner=self.gh,
                    accepted_queue_checker=of._queue(self.queue))

    def caller_snapshot(self):
        return build_pr_status_snapshot(pr_number=of.PR, repo=of.REPO_NAME, receipt_verified=True,
                                        expected_base_sha=self.h.live_base, runner=self.gh, require_ci_evidence=True)

    def write(self, snapshot):
        writer_module, writer, _ = _magma_api()
        self.n += 1
        try:
            result = writer(pr_status=snapshot, out_dir=self.tmp / f"magma{self.n}", consensus_proposal_id=of.TASK,
                            from_agent="codex-lead-1", bridge_task_id=of.TASK, carry_forward=self.request,
                            **self.live())
        except writer_module.BridgeConsensusMergeReceiptError as exc:
            report = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"errors": [str(exc)]}
            return None, "refuse", f"{report.get('decision')}: {report.get('errors')}"
        except (ValueError, of.ci_harness.PrStatusSnapshotError) as exc:
            return None, "refuse", f"raised {type(exc).__name__}: {exc}"
        assert result["carry_semantic_verification"]["ok"] is True
        return Path(result["receipt_bundle_path"]), "accept", result["decision"]

    def consume(self, manifest):
        _, _, consumer = _magma_api()
        verdict = consumer(manifest_path=manifest, carry_forward=self.request, pr_number=of.PR, task_id=of.TASK,
                           **self.live())
        return ("accept" if verdict.get("ok") is True else "refuse"), verdict.get("errors") or verdict.get("decision")

    def reseal(self, manifest, mutate):
        """Attacker with write access re-emits an integrity-valid bundle through the writer's own bundle step."""
        from datetime import datetime, timezone
        from tools.verify_magma_receipt import verify_manifest
        writer_module, _, _ = _magma_api()
        payload = json.loads((manifest.parent / "payload-001-merge.json").read_bytes())
        mutate(payload)
        self.n += 1
        bundle = writer_module._write_receipt_bundle(payload=payload, bridge_consensus=payload["bridge_consensus"],
                                                     out_dir=self.tmp / f"resealed{self.n}",
                                                     now_utc=datetime.now(timezone.utc))
        resealed = Path(bundle["manifest"])
        assert verify_manifest(resealed)["ok"] is True, "integrity alone must still verify"
        return resealed


def _env_mutation(m, case):
    if case == "stale_queue_incomplete":
        m.queue = False
    elif case == "stale_ci_failed_at_proposed_head":
        m.gh.runs[-1]["conclusion"] = "failure"
    elif case == "stale_ci_pending_at_proposed_head":
        m.gh.runs[-1].update(status="in_progress", conclusion=None)
    elif case == "stale_ci_runs_at_approved_head":
        for run in m.gh.runs:
            run["head_sha"] = m.h.old
    elif case == "stale_ci_required_set_grew":
        m.gh.protection["contexts"].append("late-required-check")
        m.gh.protection["checks"].append({"context": "late-required-check", "app_id": 15368})
    else:
        raise AssertionError(case)


def _ledger_mutation(m, case):
    _, e = MUTATIONS[case](SimpleNamespace(h=m.h), {}, {"events": copy.deepcopy(m.events)})
    m.events = e["events"]


def _receipt_mutation(m, case):
    def mutate(payload):
        r = payload["carry_forward"]["approval_receipt"]
        new_r, _ = MUTATIONS[case](SimpleNamespace(h=m.h), r, {"events": copy.deepcopy(m.events)})
        payload["carry_forward"]["approval_receipt"] = new_r
    return mutate


def _p_magma_payload_pr_number_float(m, payload):
    payload["pr_number"] = float(payload["pr_number"])


def _p_magma_payload_head_uppercase(m, payload):
    payload["head_sha"] = payload["head_sha"].upper()


def _p_magma_payload_review_origin_base_to_live_base(m, payload):
    payload["carry_forward"]["review_origin"]["review_basis"]["base_sha"] = m.h.live_base


def _p_magma_payload_ci_evidence_run_conclusion_case(m, payload):
    payload["carry_forward"]["ci_evidence"]["runs"][0]["conclusion"] = "SUCCESS"


PAYLOAD_MUTATIONS = {name[3:]: fn for name, fn in globals().items() if name.startswith("_p_")}
MAGMA_CASES = sorted(MAGMA["routes"])


def _magma_log(record):
    import os
    path = os.environ.get("F5_MAGMA_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def test_magma_routes_cover_every_verdict_case():
    verdict_cases = {n for n, s in CASES["cases"].items() if s["class"] != "apply"}
    assert set(MAGMA["routes"]) == verdict_cases
    assert {n for n, r in MAGMA["routes"].items() if r == "payload"} == set(PAYLOAD_MUTATIONS)
    assert set(MAGMA["routes"].values()) <= set(MAGMA["route_meaning"])


@pytest.mark.parametrize("case", MAGMA_CASES)
def test_magma_writer_and_consumer(magma_history, tmp_path, monkeypatch, case):
    monkeypatch.setattr(of, "CHANGED_PATH", MAGMA_PATH)  # claim scope + synthetic GitHub path (adaptation)
    spec, route = CASES["cases"][case], MAGMA["routes"][case]
    outcomes, notes, gh_seen = {}, {}, []
    if route in ("ledger", "env"):  # the same change before the writer runs: the writer must decide alike
        m = _Magma(magma_history, tmp_path / "before")
        snapshot = m.caller_snapshot()  # caller snapshot taken before the change (stale caller input)
        (_ledger_mutation if route == "ledger" else _env_mutation)(m, case)
        _, outcomes["writer"], notes["writer"] = m.write(snapshot)
        gh_seen.append(m.gh)
    m = _Magma(magma_history, tmp_path / "after")
    if route == "tail":
        m.events = m.events + [_ev("claude-rco-2", "message", "info", None, 50, task="claude-rco-2/unrelated-note")]
    manifest, written, notes["legit_writer"] = m.write(m.caller_snapshot())
    gh_seen.append(m.gh)
    assert written == "accept", notes["legit_writer"]
    if route in ("ledger", "ledger_after", "env"):
        (_env_mutation if route == "env" else _ledger_mutation)(m, case)
    elif route == "receipt":
        manifest = m.reseal(manifest, _receipt_mutation(m, case))
    elif route == "payload":
        manifest = m.reseal(manifest, lambda payload: PAYLOAD_MUTATIONS[case](m, payload))
    elif route == "tail":
        def truncate(payload):
            r = payload["carry_forward"]["approval_receipt"]
            assert r["accepted_event_count"] == len(m.events)
            _prefix(r, {"events": m.events}, r["accepted_event_count"] - 1)
        manifest = m.reseal(manifest, truncate)
    outcomes["consumer"], notes["consumer"] = m.consume(manifest)
    _magma_log({"case": case, "route": route, "expect": spec["expect"], "outcomes": outcomes, "notes": notes})
    for gh in gh_seen:
        assert of._merge_calls(gh) == [], "writer and consumer never merge"
    bad = {k: v for k, v in outcomes.items() if v != spec["expect"]}
    if bad:
        pytest.fail(f"actual defect: {case} ({spec['class']}, route {route}) intended {spec['expect']}, got {bad}; "
                    f"notes {json.dumps(notes, default=str)[:600]}")
