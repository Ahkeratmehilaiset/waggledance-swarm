"""Per-task coverage and refusal-count diagnostic for content memory build receipts (synthetic inputs only).

The diagnostic reuses the public ``build_content_projection`` per approved task, so every count is exactly what a
full build counts for that task; it reports counts and closed reason codes only, never event text, refs or ids.
Content drop classes are one generic category per task (existence of a sensitive class is not disclosed per task),
unknown refusal classes are one ``unsupported`` count and forbid a MATCH, silently excluded review-signal rows stay
uncounted exactly as in the projection, and a report over an existing projection derives its task ids from the
hash-pinned policy. Re-derivation against caller-supplied rows, roster and policy is named exactly that
(``rederivation`` MATCH); source authenticity and freshness stay UNKNOWN; an unrederived projection is refused unless
the caller explicitly accepts an untrusted, non-publishable report; identifier-policy options are validated and passed
through to every build and to the re-derivation."""

import copy
import hashlib
import hmac
import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_shared_content_projection as fx
from tools import wd_q4_task_coverage_diagnostic as diag

LEAD, TOOLS, FABLE, RCO1 = fx.LEAD, fx.TOOLS, fx.FABLE, fx.RCO1
TASK_A = fx.TASK
TASK_B = "synthetic-content-task-v2"
TASK_C = "synthetic-content-task-v3-bare"  # no drop-token word (such as empty) inside a task id
TASKS = [TASK_A, TASK_B, TASK_C]
FOREIGN_TASK = "synthetic-not-approved"
SEALED_WORD = "".join(("se", "aled"))  # assembled so the test file itself carries no marker word in clear
GENERIC = getattr(diag, "CONTENT_REFUSED", "content_refused")  # literal fallbacks keep the RED run collectable
UNSUPPORTED = getattr(diag, "UNSUPPORTED", "unsupported")
IDENTIFIER_NAME = "SyntheticNameForTest"


def policy():
    return fx.pinned(fx.policy_doc(approved_tasks=TASKS))


def identifier_binding(name=IDENTIFIER_NAME):
    raw = json.dumps({"schema": "wd.mind-identifier-policy.v1", "identifiers": [name]}).encode()
    key = b"synthetic-key-for-test-0123456789"
    return raw, key, hmac.new(key, raw, hashlib.sha256).hexdigest()


def rows():
    """Three approved tasks: A has two kept rows and one personal drop, B has one kept row, one row of a type the
    policy does not list, a row about the restricted class and a personal drop, C has no rows at all; one row of an
    unapproved task; one reviewer-authored row and one review-signal typed row (both silently excluded, never
    counted)."""
    events = [
        fx.row("task a first synthetic work note", minute=1),
        fx.row("task a second synthetic work note", agent=TOOLS, minute=2),
        fx.row("task a personal note: call +358 40 1234567 tomorrow", agent=FABLE, minute=3),
        fx.row("task b synthetic work note", minute=4, task_id=TASK_B),
        fx.row("task b intent typed row", minute=5, task_id=TASK_B, type="intent"),
        fx.row("task b row about the " + SEALED_WORD + " material class", agent=TOOLS, minute=6, task_id=TASK_B),
        fx.row("row of an unapproved task", minute=7, task_id=FOREIGN_TASK),
        fx.row("reviewer row is silently excluded", agent=RCO1, minute=8),
        fx.row("decision typed review signal row is silently excluded", minute=9, type="decision"),
        fx.row("task b personal note: call +358 40 7654321 later", agent=FABLE, minute=10, task_id=TASK_B),
    ]
    return fx.rows_of(*events)


def coverage(**over):
    raw, pin = policy()
    kwargs = dict(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    kwargs.update(over)
    return diag.task_coverage(**kwargs)


def full_build(**over):
    raw, pin = policy()
    projection = cp.build_content_projection(enabled=True, rows=rows(), policy_bytes=raw, policy_sha256=pin,
                                             roster=fx.ROSTER, **over)
    return raw, pin, projection


def from_projection(projection, **over):
    raw, pin = policy()
    kwargs = dict(policy_bytes=raw, policy_sha256=pin)
    kwargs.update(over)
    return diag.coverage_from_projection(projection, **kwargs)


def rederived(projection, **over):
    return from_projection(projection, rows=rows(), roster=fx.ROSTER, **over)


def aggregated(refused):
    """The expected aggregation, computed independently of the module: row tokens stay, drop classes merge."""
    out = {}
    for token, count in refused.items():
        key = token if token in cp.ROW_TOKENS else GENERIC
        out[key] = out.get(key, 0) + count
    return dict(sorted(out.items()))


def assert_trust_fields(report):
    assert report["source_authenticity"] == "UNKNOWN" and report["freshness"] == "UNKNOWN"
    assert report["access_control"] == diag.ACCESS_CONTROL
    assert "not access control" in report["access_control"]
    assert "VERIFIED" not in json.dumps(report)


# --- task_coverage -----------------------------------------------------------------------------------------------

def test_per_task_counts_match_a_full_build_per_task():
    report = coverage()
    assert report["schema"] == diag.SCHEMA
    assert set(report["tasks"]) == set(TASKS)
    assert report["tasks"][TASK_A] == {"accepted": 2, "refused": {GENERIC: 1}}
    assert report["tasks"][TASK_B] == {"accepted": 1, "refused": {GENERIC: 2, "type_not_allowed": 1}}
    assert report["tasks"][TASK_C] == {"accepted": 0, "refused": {}}
    assert report["totals"] == {"accepted": 3, "refused": {GENERIC: 3, "task_not_approved": 1,
                                                           "type_not_allowed": 1}}
    assert report["label"] == diag.LABEL == "counts diagnostic, not a coverage guarantee"
    assert report["audience"] == diag.AUDIENCE
    assert report["unsupported_refusals"] is False
    assert_trust_fields(report)


def test_zero_coverage_tasks_are_named_and_sorted():
    report = coverage()
    assert report["zero_coverage_tasks"] == [TASK_C]
    raw, pin = fx.pinned(fx.policy_doc(approved_tasks=[TASK_C, TASK_B, "synthetic-also-empty"]))
    report = diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert report["zero_coverage_tasks"] == sorted([TASK_C, "synthetic-also-empty"])


def test_totals_equal_the_full_build_after_the_same_aggregation_and_unapproved_rows_are_one_bucket():
    raw, pin, full = full_build()
    report = diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert full["refused"] == {"personal": 2, "sealed": 1, "task_not_approved": 1, "type_not_allowed": 1}
    assert report["totals"] == {"accepted": full["accepted"], "refused": aggregated(full["refused"])}
    assert report["unapproved_rows"] == {"accepted": 0, "refused": {"task_not_approved": 1}}
    assert report["policy_sha256"] == pin
    assert report["match"] == "MATCH"


def test_silent_review_signal_rows_leave_no_count_anywhere():
    report = coverage()
    dumped = json.dumps(report)
    assert "silent" not in dumped and "review" not in dumped and RCO1 not in dumped
    counted = sum(report["totals"]["refused"].values()) + report["totals"]["accepted"]
    assert counted == 8  # ten rows: the reviewer row and the decision-typed row leave no count anywhere


def test_sensitive_drop_classes_never_appear_per_task_or_in_totals():
    report = coverage()
    dumped = json.dumps(report).replace('"identifier_policy"', "")  # the option field name is not a class count
    for token in cp.DROP_TOKENS:
        assert token not in dumped, token
    allowed = cp.ROW_TOKENS | {GENERIC, UNSUPPORTED}
    for entry in list(report["tasks"].values()) + [report["unapproved_rows"], report["totals"]]:
        assert set(entry) == {"accepted", "refused"}
        assert type(entry["accepted"]) is int
        assert set(entry["refused"]) <= allowed
        assert all(type(n) is int and n > 0 for n in entry["refused"].values())


def test_output_carries_counts_and_reason_codes_only_and_never_echoes_caller_labels():
    report = coverage()
    dumped = json.dumps(report)
    for text in ("synthetic work note", "1234567", SEALED_WORD + " material", "intent typed", "decision typed"):
        assert text not in dumped
    for ref in rows():
        assert ref not in dumped
    assert FOREIGN_TASK not in dumped


@pytest.mark.parametrize("bad", [None, [], "rows", {"not-hex": {}}])
def test_malformed_rows_refuse_before_any_count(bad):
    with pytest.raises(diag.CoverageRefused) as info:
        coverage(rows=bad)
    assert str(info.value) == "rows_refused"


@pytest.mark.parametrize("given", [lambda: rows(), lambda: {}])
def test_policy_pin_mismatch_refuses_before_any_count(given):
    with pytest.raises(diag.CoverageRefused) as info:
        coverage(rows=given(), policy_sha256="0" * 64)
    assert str(info.value) == "policy_refused"


@pytest.mark.parametrize("roster", [None, {}, {"not-a-lane": fx.ROSTER[LEAD]}, {LEAD: "not-a-uuid"}, "roster"])
@pytest.mark.parametrize("given", [lambda: rows(), lambda: {}])
def test_an_invalid_roster_refuses_even_with_no_rows(roster, given):
    with pytest.raises(diag.CoverageRefused) as info:
        coverage(rows=given(), roster=roster)
    assert str(info.value) == "roster_refused"


def test_no_rows_at_all_reports_every_task_as_zero_coverage():
    report = coverage(rows={})
    assert report["tasks"] == {task: {"accepted": 0, "refused": {}} for task in TASKS}
    assert report["zero_coverage_tasks"] == sorted(TASKS)
    assert report["totals"] == {"accepted": 0, "refused": {}} and report["match"] == "MATCH"


def test_match_reports_mismatch_when_the_full_build_disagrees_with_the_per_task_sums(monkeypatch):
    real = cp.build_content_projection
    everything = len(rows())

    def skewed(**kwargs):
        projection = real(**kwargs)
        if len(kwargs["rows"]) == everything:
            projection = dict(projection, accepted=projection["accepted"] + 1)
        return projection

    monkeypatch.setattr(diag.cp, "build_content_projection", skewed)
    report = coverage()
    assert report["match"] == "MISMATCH"
    assert report["totals"]["accepted"] == 3
    assert report["unsupported_refusals"] is False


def test_an_unknown_refusal_class_is_one_unsupported_count_and_forbids_a_match(monkeypatch):
    real = cp.build_content_projection

    def novel(**kwargs):
        projection = real(**kwargs)
        refused = dict(projection["refused"])
        refused["novel_class_xyz"] = 1
        return dict(projection, refused=refused)

    monkeypatch.setattr(diag.cp, "build_content_projection", novel)
    report = coverage()
    assert report["tasks"][TASK_A]["refused"] == {GENERIC: 1, UNSUPPORTED: 1}
    assert report["tasks"][TASK_C] == {"accepted": 0, "refused": {}}  # no build for an empty bucket
    assert report["totals"]["refused"][UNSUPPORTED] == 3  # task a, task b, the unapproved bucket
    assert report["unsupported_refusals"] is True
    assert report["match"] == "UNSUPPORTED"
    assert "novel_class_xyz" not in json.dumps(report)


@pytest.mark.parametrize("refused", [{"personal": 0}, {5: 1}, {"personal": "1"}, [("personal", 1)]])
def test_a_malformed_refusal_entry_still_refuses(monkeypatch, refused):
    real = cp.build_content_projection
    monkeypatch.setattr(diag.cp, "build_content_projection", lambda **kw: dict(real(**kw), refused=refused))
    with pytest.raises(diag.CoverageRefused) as info:
        coverage()
    assert str(info.value) == "projection_refused"


def test_default_off_reads_nothing():
    assert coverage(enabled=False) == {"status": "OFF"}


def test_identifier_policy_options_reach_every_per_task_build(monkeypatch):
    monkeypatch.setattr(cp, "_local_identifiers", lambda: pytest.fail("environment fallback"))
    data = identifier_binding()
    named = fx.rows_of(fx.row("task a work with " + IDENTIFIER_NAME, minute=1),
                       fx.row("task a ordinary work", agent=TOOLS, minute=2),
                       fx.row("task b ordinary work", minute=3, task_id=TASK_B))
    report = coverage(rows=named, require_identifier_policy=True, identifier_policy_binding=data)
    assert report["tasks"][TASK_A] == {"accepted": 1, "refused": {GENERIC: 1}}
    assert report["tasks"][TASK_B] == {"accepted": 1, "refused": {}}
    assert report["totals"] == {"accepted": 2, "refused": {GENERIC: 1}}
    assert report["match"] == "MATCH"
    assert IDENTIFIER_NAME not in json.dumps(report)


@pytest.mark.parametrize("options", [dict(require_identifier_policy=True),
                                     dict(require_identifier_policy=True, identifier_policy_binding=(b"x",)),
                                     dict(require_identifier_policy=True, identifier_policy_binding=(b"x", b"k" * 32, "0" * 64)),
                                     dict(require_identifier_policy="yes"),
                                     dict(require_identifier_policy=False, identifier_policy_binding=identifier_binding())])
def test_invalid_identifier_policy_options_refuse_before_any_build(monkeypatch, options):
    monkeypatch.setattr(diag.cp, "build_content_projection", lambda **kw: pytest.fail("built before validation"))
    with pytest.raises(diag.CoverageRefused) as info:
        coverage(**options)
    assert str(info.value) == "identifier_policy_refused"


# --- coverage_from_projection --------------------------------------------------------------------------------------

def test_an_unrederived_projection_is_refused_unless_the_caller_accepts_an_untrusted_report():
    raw, pin, full = full_build()
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(full)
    assert str(info.value) == "untrusted_refused"
    report = from_projection(full, allow_untrusted=True)
    assert report["schema"] == diag.PROJECTION_SCHEMA
    assert report["trust"] == "untrusted" and report["rederivation"] == "NONE"
    assert report["distribution"] == "do_not_publish"
    assert report["label"] == diag.LABEL_UNTRUSTED and "untrusted" in report["label"]
    assert report["records_per_task"] == {TASK_A: 2, TASK_B: 1, TASK_C: 0}
    assert report["zero_coverage_tasks"] == [TASK_C]
    assert report["accepted"] == full["accepted"] == 3
    assert report["refused"] == aggregated(full["refused"])
    assert report["policy_sha256"] == pin
    assert report["audience"] == diag.AUDIENCE and report["unsupported_refusals"] is False
    assert_trust_fields(report)
    dumped = json.dumps(report)
    assert "synthetic work note" not in dumped and full["records"][0]["record_id"] not in dumped
    for token in cp.DROP_TOKENS:
        assert token not in dumped.replace('"identifier_policy"', "")


@pytest.mark.parametrize("value", ["yes", 1, None, [True]])
def test_the_untrusted_acceptance_must_be_a_real_boolean(value):
    raw, pin, full = full_build()
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(full, allow_untrusted=value)
    assert str(info.value) == "options_refused"


def test_a_projection_re_derived_from_pinned_rows_is_named_rederivation_match_and_a_tampered_one_refuses():
    raw, pin, full = full_build()
    report = rederived(full)
    assert report["trust"] == "rederived" and report["rederivation"] == "MATCH"
    assert report["distribution"] == "receipt_only"
    assert report["label"] == diag.LABEL
    assert report["records_per_task"] == {TASK_A: 2, TASK_B: 1, TASK_C: 0}
    assert_trust_fields(report)
    tampered = copy.deepcopy(full)
    tampered["records"][0]["meta"]["task_id"] = TASK_C
    with pytest.raises(diag.CoverageRefused) as info:
        rederived(tampered)
    assert str(info.value) == "projection_refused"
    fewer = rows()
    fewer.pop(sorted(fewer)[0])
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(full, rows=fewer, roster=fx.ROSTER)
    assert str(info.value) == "projection_refused"


def test_rows_without_a_roster_or_a_roster_without_rows_cannot_claim_a_rederivation():
    raw, pin, full = full_build()
    for extra in ({"rows": rows()}, {"roster": fx.ROSTER}, {"rows": rows(), "roster": None}):
        with pytest.raises(diag.CoverageRefused) as info:
            from_projection(full, allow_untrusted=True, **extra)
        assert str(info.value) == "projection_refused"


def test_a_projection_built_under_another_policy_pin_refuses():
    raw, pin, full = full_build()
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(dict(full, policy_sha256="0" * 64), allow_untrusted=True)
    assert str(info.value) == "projection_refused"
    other_raw, other_pin = fx.pinned(fx.policy_doc(approved_tasks=[TASK_A]))
    with pytest.raises(diag.CoverageRefused) as info:
        diag.coverage_from_projection(full, policy_bytes=other_raw, policy_sha256=other_pin, allow_untrusted=True)
    assert str(info.value) == "projection_refused"


def test_a_record_of_a_task_outside_the_pinned_policy_refuses_and_its_label_is_never_echoed():
    raw, pin, full = full_build()
    foreign = copy.deepcopy(full)
    foreign["records"][0]["meta"]["task_id"] = "synthetic-foreign-label-never-echoed"
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(foreign, allow_untrusted=True)
    assert str(info.value) == "projection_refused"
    assert "foreign-label" not in str(info.value)


@pytest.mark.parametrize("field", ["agent", "to"])
@pytest.mark.parametrize("lane", [fx.RCO1, fx.RCO2])
def test_a_record_authored_by_or_addressed_to_a_review_lane_refuses_the_projection(field, lane):
    raw, pin, full = full_build()
    reviewer = copy.deepcopy(full)
    reviewer["records"][0]["meta"][field] = lane
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(reviewer, allow_untrusted=True)
    assert str(info.value) == "projection_refused"
    with pytest.raises(diag.CoverageRefused) as info:
        rederived(reviewer)
    assert str(info.value) == "projection_refused"


def test_an_unknown_refusal_class_in_a_projection_is_unsupported_and_a_malformed_one_refuses():
    raw, pin, full = full_build()
    novel = dict(full, refused=dict(full["refused"], novel_class_xyz=2))
    report = from_projection(novel, allow_untrusted=True)
    assert report["refused"][UNSUPPORTED] == 2 and report["unsupported_refusals"] is True
    assert "novel_class_xyz" not in json.dumps(report)
    with pytest.raises(diag.CoverageRefused):
        from_projection(dict(full, refused={"personal": 0}), allow_untrusted=True)


def test_receipt_wrapper_with_a_messages_projection_is_read_the_same_way():
    raw, pin, full = full_build()
    wrapped = {"schema": "synthetic-wrapper", "documents": None, "messages": full}
    assert from_projection(wrapped, allow_untrusted=True) == from_projection(full, allow_untrusted=True)
    assert rederived(wrapped) == rederived(full)
    with pytest.raises(diag.CoverageRefused):
        from_projection({"messages": "not a projection"}, allow_untrusted=True)


def _projection_variants():
    raw, pin, full = full_build()
    return [None, "x", {"records": "no"}, {"records": []}, dict(full, schema="other"), dict(full, accepted=99),
            dict(full, review_signal_excluded=False), dict(full, extra_key=1),
            dict(full, records=full["records"] + [{}]), dict(full, records=full["records"] + [{"meta": {}}])]


@pytest.mark.parametrize("bad", _projection_variants())
def test_malformed_or_unexpected_projection_shape_refuses(bad):
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(bad, allow_untrusted=True)
    assert str(info.value) == "projection_refused"


def test_policy_pin_mismatch_refuses_a_projection_report_before_reading_it():
    raw, pin, full = full_build()
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(full, policy_sha256="0" * 64, allow_untrusted=True)
    assert str(info.value) == "policy_refused"


def test_identifier_policy_binding_is_passed_into_the_rederivation():
    data = identifier_binding()
    raw, pin, full = full_build(require_identifier_policy=True, identifier_policy_binding=data)
    assert "identifier_policy_hmac" in full
    report = rederived(full, require_identifier_policy=True, identifier_policy_binding=data)
    assert report["rederivation"] == "MATCH" and report["records_per_task"] == {TASK_A: 2, TASK_B: 1, TASK_C: 0}
    assert report["identifier_policy"] == "bound"
    with pytest.raises(diag.CoverageRefused) as info:
        rederived(full, require_identifier_policy=True, identifier_policy_binding=identifier_binding("OtherName"))
    assert str(info.value) == "projection_refused"
    plain = rederived(full_build()[2])
    assert plain["identifier_policy"] == "none"


def test_a_projection_carrying_an_identifier_hmac_without_a_binding_refuses_explicitly():
    raw, pin, full = full_build(require_identifier_policy=True, identifier_policy_binding=identifier_binding())
    for call in (lambda: from_projection(full, allow_untrusted=True), lambda: rederived(full)):
        with pytest.raises(diag.CoverageRefused) as info:
            call()
        assert str(info.value) == "identifier_policy_refused"
    plain = full_build()[2]
    with pytest.raises(diag.CoverageRefused) as info:
        rederived(plain, require_identifier_policy=True, identifier_policy_binding=identifier_binding())
    assert str(info.value) == "identifier_policy_refused"  # a binding for a projection built without one


@pytest.mark.parametrize("options", [dict(require_identifier_policy=True),
                                     dict(require_identifier_policy="yes"),
                                     dict(require_identifier_policy=False, identifier_policy_binding=identifier_binding())])
def test_invalid_identifier_policy_options_refuse_a_projection_report(options):
    raw, pin, full = full_build()
    with pytest.raises(diag.CoverageRefused) as info:
        from_projection(full, allow_untrusted=True, **options)
    assert str(info.value) == "identifier_policy_refused"


def test_report_is_canonical_json_serializable_and_deterministic():
    one, two = coverage(), coverage()
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    digest = hashlib.sha256(json.dumps(one, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    assert len(digest) == 64
