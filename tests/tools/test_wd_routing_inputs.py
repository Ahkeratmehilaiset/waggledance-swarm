"""F19 W1: the pure input assembler hands compose() exactly what the evidence proves, and nothing else."""
import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.wd_routing_capacity as rc
import tools.wd_task_router as tr
from tools import wd_routing_inputs as wi
from tools.wd_composer_select import digest
from tools.wd_capacity_pacing import MAX_SAMPLE_AGE_SECONDS, pace_windows
from tools.wd_routing_capacity import COMPOSED_SCHEMA, compose

NOW = datetime(2026, 9, 30, 19, 30, tzinfo=timezone.utc)
COMPOSE_PARAMETERS = {"task", "workers", "subjects", "rows", "paced", "signed_policy", "prepared_artifacts",
                      "routing_policy", "shadow_weights"}


def _lane(worker, subject="auth-" + "1" * 8, kind="lane", profile="codex-gpt-6.1-sol-xhigh", **extra):
    record = {"schema": wi.LANE_EVIDENCE_SCHEMA, "worker": worker, "kind": kind, "profile_id": profile,
              "subject": subject, "observed_utc": (NOW - timedelta(seconds=60)).isoformat()}
    record.update(extra)
    return record


def _task():
    return {"schema": "wd.routing-task.v1", "task_id": "codex-lead-1/w1", "revision": "v1",
            "input_digest": "a" * 64, "task_class": "implementation", "scope": ["repo:tools/x.py"],
            "author": "codex-lead-1", "created_utc": (NOW - timedelta(minutes=5)).isoformat()}


def _assemble(lanes, **overrides):
    arguments = {"task": _task(), "lanes": lanes, "rows": [], "paced": [], "prepared_artifacts": [],
                 "routing_policy": {"schema": "wd.routing-policy.v1"}, "now": NOW}
    arguments.update(overrides)
    return wi.assemble(**arguments)


def test_the_inputs_are_exactly_the_compose_parameters_and_carry_no_authority():
    result = _assemble([_lane("fable-5"), _lane(wi.GROK, subject=None, kind=wi.GROK, profile="grok-4.7")])
    assert set(result["inputs"]) == COMPOSE_PARAMETERS
    assert (result["schema"], result["authority"], result["execution_allowed"]) == (wi.SCHEMA, "none", False)
    assert result["now_utc"] == NOW.isoformat() and result["reasons"] == []
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["fable-5", "grok"]
    for worker in result["inputs"]["workers"]:
        assert set(worker) == {"schema", "worker", "kind", "profile_id"}   # never capacity, load or a guess
    assert result["inputs"]["subjects"] == {"fable-5": "auth-" + "1" * 8}   # Grok never gets a subject


def test_capacity_in_lane_evidence_is_malformed_so_capacity_only_comes_through_the_adapter():
    result = _assemble([_lane("fable-5", capacity={"state": "ok"})])
    assert result["inputs"]["workers"] == [] and result["inputs"]["subjects"] == {}
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": ["lane_evidence_malformed"]}]


@pytest.mark.parametrize("record, reason", [
    (_lane("someone-else"), "foreign_worker"),
    (_lane("fable-5", kind=wi.GROK), "kind_mismatch"),
    (_lane(wi.GROK, kind="lane"), "kind_mismatch"),
    (_lane(wi.GROK, kind=wi.GROK, profile="grok-4.7"), "grok_subject_refused"),
    (_lane("fable-5", subject=""), "subject_malformed"),
    (_lane("fable-5", subject=["auth"]), "subject_malformed"),
    (_lane("fable-5", profile=""), "lane_evidence_malformed"),
    ({**_lane("fable-5"), "schema": "wd.routing-lane-evidence.v0"}, "lane_evidence_malformed"),
    ({key: value for key, value in _lane("fable-5").items() if key != "subject"}, "lane_evidence_malformed"),
    ("fable-5", "lane_evidence_malformed"),
])
def test_foreign_or_malformed_evidence_never_becomes_a_worker_or_a_subject(record, reason):
    result = _assemble([record])
    assert (result["inputs"]["workers"], result["inputs"]["subjects"]) == ([], {})
    assert result["unknown"][0]["reasons"] == [reason]


def test_duplicate_evidence_for_one_worker_is_ambiguous_and_neither_copy_is_used():
    result = _assemble([_lane("fable-5"), _lane("fable-5", subject="auth-" + "2" * 8), _lane("claude-rco-1")])
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["claude-rco-1"]
    assert "fable-5" not in result["inputs"]["subjects"]
    assert [(u["index"], u["reasons"]) for u in result["unknown"]] == [
        (0, ["lane_evidence_ambiguous"]), (1, ["lane_evidence_ambiguous"])]


def test_a_missing_subject_keeps_the_worker_but_never_invents_a_subject():
    result = _assemble([_lane("fable-5", subject=None)])
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["fable-5"]
    assert result["inputs"]["subjects"] == {}
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": ["subject_unknown"]}]


def test_role_and_qualification_are_copied_only_when_they_belong_to_the_worker():
    role = {"worker": "fable-5", "verified": True, "roles": ["producer"], "observed_utc": NOW.isoformat()}
    qualification = [{"task_class": "implementation", "profile_id": "codex-gpt-6.1-sol-xhigh"}]
    result = _assemble([_lane("fable-5", role=role, qualification=qualification),
                        _lane("claude-rco-1", role={**role}, qualification="not-a-list")])
    fable, rco = result["inputs"]["workers"][1], result["inputs"]["workers"][0]
    assert (fable["role"], fable["qualification"]) == (role, qualification)
    assert fable["role"] is not role and fable["qualification"] is not qualification   # copies, never shared
    assert "role" not in rco and "qualification" not in rco
    assert result["unknown"] == [{"index": 1, "worker": "claude-rco-1",
                                  "reasons": ["role_foreign_or_malformed", "qualification_malformed"]}]


def test_documents_pass_through_unchanged_and_the_task_is_never_altered():
    task, rows, paced, attempts = _task(), [{"provider": "codex"}], [{"window": "5h"}], [{"attempt_id": "x"}]
    policy, signed, shadow = {"schema": "p"}, {"schema": "wd.routing-capacity-policy.v1"}, {"weights": {}}
    before = copy.deepcopy((task, rows, paced, attempts, policy, signed, shadow))
    result = _assemble([_lane("fable-5")], task=task, rows=rows, paced=paced, prepared_artifacts=attempts,
                       routing_policy=policy, signed_policy=signed, shadow_weights=shadow)
    inputs = result["inputs"]
    assert (inputs["task"], inputs["rows"], inputs["paced"]) == (task, rows, paced)
    assert inputs["prepared_artifacts"] == attempts
    assert (inputs["routing_policy"], inputs["signed_policy"], inputs["shadow_weights"]) == (policy, signed, shadow)
    assert (task, rows, paced, attempts, policy, signed, shadow) == before


def test_a_missing_signed_policy_stays_none_and_a_supplied_one_is_never_called_verified():
    absent = _assemble([_lane("fable-5")])
    assert absent["inputs"]["signed_policy"] is None
    supplied = _assemble([_lane("fable-5")], signed_policy={"schema": "wd.routing-capacity-policy.v1"})
    for result in (absent, supplied):
        assert result["provenance"]["signed_policy"]["signature_verified"] is False


def test_provenance_is_the_parsed_document_digest_and_caller_bytes_stay_unverified():
    sources = {"rows": {"path": "C:/collector/rows.json", "byte_sha256": "b" * 64},
               "task": {"path": "C:/t.json", "byte_sha256": "B" * 64}}
    result = _assemble([_lane("fable-5")], rows=[{"provider": "codex"}], sources=sources)
    rows = result["provenance"]["rows"]
    assert rows["document_digest"] == digest([{"provider": "codex"}]) and rows["digest_basis"] == wi.DIGEST_BASIS
    assert (rows["caller_path"], rows["caller_byte_sha256"], rows["byte_digest_verified"]) == (
        "C:/collector/rows.json", "b" * 64, False)
    assert result["provenance"]["task"]["caller_byte_sha256"] is None       # an uppercase digest is refused
    assert result["reasons"] == ["source_malformed:task"]
    assert result["provenance"]["lanes"]["document_digest"] == digest([_lane("fable-5")])
    assert set(result["provenance"]) == COMPOSE_PARAMETERS - {"workers", "subjects"} | {"lanes"}


@pytest.mark.parametrize("now", [datetime(2026, 9, 30, 19, 30), "2026-09-30T19:30:00+00:00", None])
def test_only_an_aware_clock_is_accepted_and_none_is_read(now):
    result = _assemble([_lane("fable-5")], now=now)
    assert (result["now_utc"], result["reasons"]) == (None, ["now_invalid"])
    assert result["inputs"]["workers"] == [] and result["unknown"][0]["reasons"] == ["evidence_age_unknown"]


def test_lanes_that_are_not_a_list_give_no_workers():
    result = _assemble({"fable-5": _lane("fable-5")})
    assert (result["inputs"]["workers"], result["reasons"]) == ([], ["lanes_malformed"])


def test_the_assembled_inputs_drive_compose_and_capacity_stays_the_adapters():
    result = _assemble([_lane("fable-5"), _lane("claude-rco-1", subject=None),
                        _lane(wi.GROK, subject=None, kind=wi.GROK, profile="grok-4.7")])
    composed = compose(**result["inputs"], now=NOW)
    assert (composed["schema"], composed["authority"]) == (COMPOSED_SCHEMA, "none")
    assert composed["execution_allowed"] is False
    reasons = {entry["worker"]: entry["reasons"] for entry in composed["capacity"]}
    assert reasons == {"claude-rco-1": ["subject_unknown"], "fable-5": ["observation_missing"],
                       "grok": ["no_measured_grok_capacity"]}


def test_the_module_reads_no_clock_file_environment_or_provider():
    tree = ast.parse(Path(wi.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "datetime", "json", "typing", "tools.bridge_pool_binding",
                        "tools.wd_composer_select", "tools.wd_task_router"}
    called = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not called & {"now", "utcnow", "today", "time", "open", "read_text", "read_bytes", "getenv",
                         "run", "Popen", "system", "__import__", "eval", "exec"}


@pytest.mark.parametrize("age, reason", [
    (timedelta(seconds=wi.MAX_LANE_EVIDENCE_AGE_SECONDS + 1), "lane_evidence_stale"),
    (timedelta(seconds=-1), "lane_evidence_future"),
])
def test_stale_or_future_lane_evidence_is_withheld_whole(age, reason):
    result = _assemble([_lane("fable-5", observed_utc=(NOW - age).isoformat())])
    assert (result["inputs"]["workers"], result["inputs"]["subjects"]) == ([], {})
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": [reason]}]


@pytest.mark.parametrize("observed", ["2026-09-30T19:29:00", "yesterday", 1790000000, None])
def test_an_observed_time_without_an_offset_is_malformed(observed):
    result = _assemble([_lane("fable-5", observed_utc=observed)])
    assert result["unknown"][0]["reasons"] == ["lane_evidence_malformed"]


def test_the_age_bound_is_inclusive_at_the_adapter_sample_bound():
    edge = (NOW - timedelta(seconds=wi.MAX_LANE_EVIDENCE_AGE_SECONDS)).isoformat()
    result = _assemble([_lane("fable-5", observed_utc=edge)])
    assert result["unknown"] == [] and result["inputs"]["subjects"] == {"fable-5": "auth-" + "1" * 8}


def test_a_signed_policy_that_is_not_an_object_is_withheld_not_repaired():
    result = _assemble([_lane("fable-5")], signed_policy="signed")
    assert (result["inputs"]["signed_policy"], result["reasons"]) == (None, ["signed_policy_malformed"])


def test_sources_for_an_unknown_document_are_refused_whole():
    result = _assemble([_lane("fable-5")], sources={"workers": {"path": "x", "byte_sha256": "b" * 64}})
    assert result["reasons"] == ["sources_malformed"]
    assert all(entry["caller_path"] is None for entry in result["provenance"].values())


def test_the_inputs_are_private_copies_that_a_later_caller_change_cannot_reach():
    task, rows, lanes = _task(), [{"provider": "codex"}], [_lane("fable-5", role={"worker": "fable-5"})]
    result = _assemble(lanes, task=task, rows=rows)
    task["scope"].append("repo:tools/y.py")
    rows[0]["provider"] = "claude"
    lanes[0]["role"]["worker"] = "claude-rco-1"
    inputs = result["inputs"]
    assert (inputs["task"]["scope"], inputs["rows"]) == (["repo:tools/x.py"], [{"provider": "codex"}])
    assert inputs["workers"][0]["role"] == {"worker": "fable-5"}
    assert result["provenance"]["task"]["document_digest"] == digest(inputs["task"])


def test_the_lane_age_bound_is_the_capacity_adapter_sample_bound():
    assert wi.MAX_LANE_EVIDENCE_AGE_SECONDS == MAX_SAMPLE_AGE_SECONDS == 900


def test_a_document_json_cannot_encode_is_withheld_not_passed_on_shared():
    result = _assemble([_lane("fable-5")], rows=(row for row in []))
    assert (result["inputs"]["rows"], result["reasons"]) == (None, ["document_not_canonical_json:rows"])
    assert result["provenance"]["rows"]["document_digest"] == digest(None)


# RCO2 independent review of 54bea007 (20:00:06Z): W1-F1 to W1-F4.
@pytest.mark.parametrize("name, value", [
    ("rows", [{"used_percent": float("nan")}]),
    ("rows", {"codex"}),
    ("task", {**_task(), "scope": ("repo:tools/x.py",)}),
    ("routing_policy", {1: "a non-string key"}),
], ids=["nan", "set", "tuple", "int-key"])
def test_a_document_without_an_exact_json_form_is_withheld_with_a_reason(name, value):
    result = _assemble([_lane("fable-5")], **{name: value})
    assert result["inputs"][name] is None
    assert result["reasons"] == ["document_not_canonical_json:" + name]
    assert result["provenance"][name]["document_digest"] == digest(None)


def test_a_lane_role_holding_a_tuple_withholds_the_whole_lanes_document():
    result = _assemble([_lane("fable-5", role={"worker": "fable-5", "roles": ("producer",)})])
    assert result["inputs"]["workers"] == []
    assert result["reasons"] == ["document_not_canonical_json:lanes", "lanes_malformed"]


class _SelfCopyingList(list):
    def __deepcopy__(self, memo):
        return self


def test_a_document_whose_deepcopy_returns_itself_is_still_a_private_copy():
    rows = _SelfCopyingList([{"provider": "codex"}])
    result = _assemble([_lane("fable-5")], rows=rows)
    rows.append({"provider": "claude"})
    rows[0]["provider"] = "changed"
    assert result["inputs"]["rows"] == [{"provider": "codex"}] and type(result["inputs"]["rows"]) is list
    assert result["provenance"]["rows"]["document_digest"] == digest([{"provider": "codex"}])


@pytest.mark.parametrize("field, value, reason", [
    ("subject", " ", "subject_malformed"),
    ("subject", " auth-1", "subject_malformed"),
    ("subject", "auth-1 ", "subject_malformed"),
    ("profile_id", " ", "lane_evidence_malformed"),
    ("profile_id", "p" * 129, "lane_evidence_malformed"),
])
def test_padded_whitespace_or_overlong_text_is_malformed_as_compose_would_judge_it(field, value, reason):
    record = _lane("fable-5")
    record[field] = value
    result = _assemble([record])
    assert result["inputs"]["workers"] == [] and result["unknown"][0]["reasons"] == [reason]


@pytest.mark.parametrize("value", ["a", " a", "a ", " ", "", "a\tb", "x" * 256, "x" * 257, "\u00e9", "\u2028"])
def test_every_subject_the_assembler_admits_compose_admits_too(value):
    from tools.wd_routing_capacity import _label
    assert not wi._text(value) or _label(value)


def test_grok_never_carries_a_role_or_qualification_into_the_inputs():
    role = {"worker": "grok", "verified": True, "roles": ["producer"], "observed_utc": NOW.isoformat()}
    record = _lane(wi.GROK, subject=None, kind=wi.GROK, profile="grok-4.7", role=role,
                   qualification=[{"task_class": "implementation", "profile_id": "grok-4.7"}])
    result = _assemble([record])
    [grok] = result["inputs"]["workers"]
    assert set(grok) == {"schema", "worker", "kind", "profile_id"} and grok["worker"] == "grok"
    assert result["unknown"] == [{"index": 0, "worker": "grok",
                                  "reasons": ["grok_role_refused", "grok_qualification_refused"]}]


# --- provider-typed subjects (Lead 21:09:28Z): the pool-binding grammar; compose binds the provider ---------------
HEX_SUBJECT = "a" * 64
SESSION = "sess-claude-1"
CODEX_SUBJECT = {"kind": "auth_context", "id": HEX_SUBJECT}      # the pool-binding receipt's subject shape
CLAUDE_SUBJECT = {"kind": "native_session", "id": SESSION}
PROVIDER_BOUND_COMPOSE = "profile_providers" in rc.POLICY_KEYS    # the frozen V3 compose (a132ebba)


@pytest.mark.parametrize("subject", [CODEX_SUBJECT, CLAUDE_SUBJECT, {"kind": "native_session", "id": HEX_SUBJECT}],
                         ids=["codex", "claude", "claude-hex-session"])
def test_a_typed_subject_in_the_pool_binding_grammar_is_carried_as_a_fresh_exact_copy(subject):
    lanes = [_lane("fable-5", subject=subject)]
    result = _assemble(lanes)
    assert result["unknown"] == [] and result["inputs"]["subjects"] == {"fable-5": subject}
    carried = result["inputs"]["subjects"]["fable-5"]
    assert type(carried) is dict and [type(carried["kind"]), type(carried["id"])] == [str, str]
    assert carried is not subject and carried is not lanes[0]["subject"]


TYPED_MALFORMED = [
    ("kind_unknown", {"kind": "account", "id": HEX_SUBJECT}),
    ("kind_is_the_provider", {"kind": "codex", "id": HEX_SUBJECT}),
    ("kind_case_variant", {"kind": "Auth_Context", "id": HEX_SUBJECT}),
    ("key_case_variant", {"Kind": "auth_context", "id": HEX_SUBJECT}),
    ("extra_key", {**CODEX_SUBJECT, "provider": "codex"}),
    ("missing_id", {"kind": "auth_context"}),
    ("empty", {}),
    ("hex_uppercase", {"kind": "auth_context", "id": "A" * 64}),
    ("hex_short", {"kind": "auth_context", "id": "a" * 63}),
    ("hex_kind_with_a_session_id", {"kind": "auth_context", "id": SESSION}),
    ("hex_trailing_newline", {"kind": "auth_context", "id": HEX_SUBJECT + "\n"}),
    ("session_bad_character", {"kind": "native_session", "id": "sess claude"}),
    ("session_leading_separator", {"kind": "native_session", "id": "-sess"}),
    ("session_too_long", {"kind": "native_session", "id": "s" * 129}),
    ("session_empty", {"kind": "native_session", "id": ""}),
    ("id_not_text", {"kind": "auth_context", "id": 7}),
    ("kind_not_text", {"kind": ["auth_context"], "id": HEX_SUBJECT}),
]


@pytest.mark.parametrize("subject", [case[1] for case in TYPED_MALFORMED], ids=[case[0] for case in TYPED_MALFORMED])
def test_a_malformed_typed_subject_withholds_the_lane_whole(subject):
    result = _assemble([_lane("fable-5", subject=subject)])
    assert (result["inputs"]["workers"], result["inputs"]["subjects"]) == ([], {})
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": ["subject_malformed"]}]


def test_the_longest_session_id_the_grammar_admits_is_carried():
    subject = {"kind": "native_session", "id": "s" * 128}
    assert _assemble([_lane("fable-5", subject=subject)])["inputs"]["subjects"] == {"fable-5": subject}


def test_grok_never_carries_a_typed_subject():
    result = _assemble([_lane(wi.GROK, subject=CODEX_SUBJECT, kind=wi.GROK, profile="grok-4.7")])
    assert result["unknown"][0]["reasons"] == ["grok_subject_refused"] and result["inputs"]["subjects"] == {}


class _Text(str):
    pass


class _Map(dict):
    pass


@pytest.mark.parametrize("subject", [_Map(CODEX_SUBJECT), {"kind": _Text("auth_context"), "id": HEX_SUBJECT},
                                     {"kind": "auth_context", "id": _Text(HEX_SUBJECT)}],
                         ids=["dict-subclass", "kind-subclass", "id-subclass"])
def test_the_grammar_takes_exact_types_and_assemble_hands_on_only_plain_json_types(subject):
    assert wi._typed_subject(subject) is None       # exact types, as compose's own _subject
    carried = _assemble([_lane("fable-5", subject=subject)])["inputs"]["subjects"]["fable-5"]
    assert carried == CODEX_SUBJECT and type(carried) is dict   # the canonical copy left no subclass to carry
    assert [type(carried["kind"]), type(carried["id"])] == [str, str]


def test_one_subject_object_shared_by_two_lanes_gives_two_private_copies():
    shared = dict(CODEX_SUBJECT)
    result = _assemble([_lane("fable-5", subject=shared), _lane("claude-rco-1", subject=shared)])
    subjects = result["inputs"]["subjects"]
    assert subjects["fable-5"] is not subjects["claude-rco-1"]
    assert all(value is not shared for value in subjects.values())
    shared["id"] = "b" * 64
    subjects["fable-5"]["id"] = "c" * 64
    assert subjects["claude-rco-1"] == CODEX_SUBJECT


def _routed_lane(worker, profile, subject):
    stamp = (NOW - timedelta(minutes=1)).isoformat()
    return _lane(worker, subject=subject, profile=profile,
                 role={"worker": worker, "roles": ["producer"], "verified": True, "observed_utc": stamp},
                 qualification=[{"task_class": "implementation", "profile_id": profile, "qualified": True,
                                 "observed_utc": stamp, "valid_until_utc": (NOW + timedelta(days=1)).isoformat(),
                                 "receipt_sha256": "d" * 64}])


def _twin_documents():
    """The caller's evidence for compose: rows, pacing and the signed provider policy (all caller-supplied)."""
    reset = {name: int((NOW + delta).timestamp()) for name, delta in (
        ("primary", timedelta(hours=3)), ("secondary", timedelta(days=4)),
        ("five_hour", timedelta(hours=2)), ("seven_day", timedelta(days=5)))}
    binding = {"receipt_id": "b" * 32, "receipt_sha256": "c" * 64, "provenance_kind": "operator_reading",
               "expires_at_utc": (NOW + timedelta(hours=12)).isoformat()}
    observed = (NOW - timedelta(seconds=30)).isoformat()
    codex_row = {"schema": "wd.capacity-observation.v1", "provider": "codex", "observed_at": observed,
                 "auth_context_id": HEX_SUBJECT, "account_pool": "codex-pro-a",
                 "pool_identity_state": "verified_binding", "pool_binding": binding, "freshness": "fresh",
                 "execution_allowed": False,
                 "payload": {"rateLimits": {"limitId": "codex",
                                            "primary": {"usedPercent": 31.0, "resetsAt": reset["primary"],
                                                        "windowDurationMins": 300},
                                            "secondary": {"usedPercent": 20.1, "resetsAt": reset["secondary"],
                                                          "windowDurationMins": 10080}}}}
    claude_row = {"schema": "wd.capacity-observation.v1", "provider": "claude", "source_ref": "claude:statusline",
                  "observed_at": observed, "native_thread_id": SESSION, "account_pool": "claude-max-a",
                  "pool_identity_state": "verified_binding", "pool_binding": binding,
                  "freshness": "provider_timestamp_unknown", "execution_allowed": False,
                  "payload": {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": reset["five_hour"]},
                                              "seven_day": {"used_percentage": 30, "resets_at": reset["seven_day"]}}}}
    samples = []
    for provider, window, first, last, duration in (("codex", "primary", 30.0, 31.0, 300),
                                                     ("codex", "secondary", 20.0, 20.1, 10080),
                                                     ("claude", "five_hour", 11.0, 12.0, 300),
                                                     ("claude", "seven_day", 29.9, 30.0, 10080)):
        for used, age in ((first, 41), (last, 1)):
            samples.append({"provider": provider, "limit_id": provider, "window": window, "used_percent": used,
                            "resets_at": float(reset[window]), "duration_minutes": duration,
                            "observed_at": NOW - timedelta(minutes=age)})
    body = {"schema": rc.POLICY_SCHEMA, "max_observation_age_seconds": 300,
            "accepted_freshness": {"codex": ["fresh"], "claude": ["fresh", "provider_timestamp_unknown"]},
            "pools": {"codex-pro-a": {"billing": "included", "mode": "normal"},
                      "claude-max-a": {"billing": "included", "mode": "normal"}},
            "profile_providers": {"codex-sol-high": "codex", "claude-strong": "claude"}}
    routing = {"schema": tr.POLICY_SCHEMA, "max_evidence_age_seconds": 900, "budget_mode": "steady",
               "class_roles": {c: ["producer"] for c in tr.TASK_CLASSES},
               "class_profiles": {c: ["codex-sol-high", "claude-strong"] for c in tr.TASK_CLASSES}}
    return {"rows": [claude_row, codex_row], "paced": pace_windows(samples, now=NOW),
            "signed_policy": {"policy": body, "sha256": digest(body)}, "routing_policy": routing}


def _twin(lane):
    result = _assemble([lane], **_twin_documents())
    for worker in result["inputs"]["workers"]:      # the caller's load evidence: W3's, never assembled here
        worker["load"] = {"state": "idle", "observed_utc": (NOW - timedelta(minutes=1)).isoformat()}
    return result, compose(**result["inputs"], now=NOW)


@pytest.mark.skipif(not PROVIDER_BOUND_COMPOSE, reason="needs the frozen V3 compose (a132ebba) that Lead composes")
@pytest.mark.parametrize("worker, profile, subject, pool", [
    ("codex-tools-1", "codex-sol-high", CODEX_SUBJECT, "codex-pro-a"),
    ("fable-5", "claude-strong", CLAUDE_SUBJECT, "claude-max-a"),
], ids=["codex", "claude"])
def test_an_assembled_typed_subject_routes_through_compose_on_its_own_providers_row(worker, profile, subject, pool):
    result, composed = _twin(_routed_lane(worker, profile, subject))
    assert result["reasons"] == [] and result["unknown"] == []
    assert (composed["advice"]["verdict"], composed["advice"]["recommended"]["worker"]) == (tr.ROUTE, worker)
    [record] = composed["capacity"]
    assert (record["verdict"], record["capacity"]["pool"], record["profile_id"]) == ("known", pool, profile)


@pytest.mark.skipif(not PROVIDER_BOUND_COMPOSE, reason="needs the frozen V3 compose (a132ebba) that Lead composes")
@pytest.mark.parametrize("subject, reasons", [
    (CLAUDE_SUBJECT, ["provider_mismatch"]),        # a Codex-signed profile with a Claude session subject
    (HEX_SUBJECT, ["subject_unbound"]),             # a text subject stays representable, never bound capacity
    (SESSION, ["subject_unbound"]),
], ids=["cross-provider", "text-hex", "text-session"])
def test_a_cross_provider_or_text_subject_never_becomes_capacity(subject, reasons):
    result, composed = _twin(_routed_lane("codex-tools-1", "codex-sol-high", subject))
    assert result["unknown"] == [] and result["inputs"]["subjects"] == {"codex-tools-1": subject}
    [record] = composed["capacity"]
    assert (record["verdict"], record["reasons"], record["capacity"]) == ("unknown", reasons, None)
    assert composed["advice"]["verdict"] != tr.ROUTE


def test_a_cross_provider_typed_subject_never_becomes_capacity_with_either_compose():
    result, composed = _twin(_routed_lane("codex-tools-1", "codex-sol-high", CLAUDE_SUBJECT))
    [record] = composed["capacity"]
    assert (record["verdict"], record["capacity"]) == ("unknown", None)
    assert composed["advice"]["verdict"] != tr.ROUTE
