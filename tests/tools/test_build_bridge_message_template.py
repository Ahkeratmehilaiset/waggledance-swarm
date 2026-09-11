# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/build_bridge_message_template.py.

The builder renders a strict, compact bridge event TEMPLATE. It never appends to
the bridge, never verifies an identity and never grants an approval; every test
here checks either a successful template or a rejected invalid input.
"""
from __future__ import annotations

import builtins
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_bridge_message_template as builder  # noqa: E402
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402

SCRIPT = TOOLS_DIR / "build_bridge_message_template.py"
WRITER_SCRIPT = ROOT / ".agent-bridge" / "bin" / "Write-AgentEvent.ps1"
HEAD = "84c6e35fd589f0994fe4c3df3bb5978178233277"
TASK = "codex-lead-1/bridge-continuity-fix-20260911"
UUID_A = "11111111-1111-4111-8111-111111111111"
SESSION = "wd-reboot-20260903T091307Z"
SUMMARY = "191 tests pass at exact head, no blocking findings"
EVIDENCE = ("tests/tools: 191 passed", "gh pr checks 1680: 6 of 6 green")
GENERATED_AT = datetime(2026, 9, 11, 8, 30, 0, tzinfo=timezone.utc)
TS_UTC = "2026-09-11T08:30:00.000000Z"


def _build(**overrides: object) -> dict:
    kwargs: dict[str, object] = {
        "kind": "rco_pass",
        "task_id": TASK,
        "agent": "claude-rco-1",
        "summary": SUMMARY,
        "head_sha": HEAD,
        "evidence": EVIDENCE,
        "to": "codex-lead-1",
        "pr": 1680,
        "agent_uuid": UUID_A,
        "session_id": SESSION,
        "role": "rco-security",
        "run_id": SESSION,
        "supersedes_event_id": "",
        "generated_at": GENERATED_AT,
    }
    kwargs.update(overrides)
    return builder.build_bridge_message_template(**kwargs)


def _error(**overrides: object) -> str:
    with pytest.raises(builder.BridgeMessageTemplateError) as excinfo:
        _build(**overrides)
    assert isinstance(excinfo.value, ValueError)
    return excinfo.value.reason


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


# --- closed vocabulary --------------------------------------------------------


def test_closed_kind_enum_and_event_shapes() -> None:
    assert builder.TEMPLATE_VERSION == "wd.bridge_message_template.v1"
    assert builder.TEMPLATE_KINDS == (
        "progress",
        "review_requested",
        "build_consensus",
        "rco_pass",
        "changes_requested",
    )
    assert builder.KIND_EVENT_SHAPES == {
        "progress": ("status", "progress"),
        "review_requested": ("wake_request", "review_requested"),
        "build_consensus": ("decision", "build_consensus_pass"),
        "rco_pass": ("decision", "rco_pass"),
        "changes_requested": ("decision", "changes_requested"),
    }
    assert tuple(builder.KIND_EVENT_SHAPES) == builder.TEMPLATE_KINDS
    assert builder.HEAD_REQUIRED_KINDS == frozenset(
        {"review_requested", "build_consensus", "rco_pass", "changes_requested"}
    )
    assert builder.TO_REQUIRED_KINDS == frozenset({"review_requested"})
    assert builder.DECISION_KINDS == frozenset(
        {"build_consensus", "rco_pass", "changes_requested"}
    )
    assert builder.RECOGNIZED_RCO_AGENTS == frozenset({"claude-rco-1", "claude-rco-2"})
    assert builder.TEMPLATE_CWD == "template_not_emitted"


def test_recognized_rco_set_locksteps_with_writer_and_gate() -> None:
    writer_text = WRITER_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"^\$rcoReviewAgents = @\(([^)]*)\)", writer_text, re.MULTILINE)
    assert match is not None, "writer rcoReviewAgents list not found"
    writer_set = frozenset(re.findall(r"'([^']+)'", match.group(1)))
    assert writer_set == builder.RECOGNIZED_RCO_AGENTS

    import check_bridge_changes_requested as gate  # noqa: PLC0415

    assert gate._RECOGNIZED_RCOS == builder.RECOGNIZED_RCO_AGENTS


# --- success path ------------------------------------------------------------


def test_rco_pass_template_matches_canonical_envelope_and_payload() -> None:
    report = _build()

    assert report["template_version"] == builder.TEMPLATE_VERSION
    assert report["kind"] == "rco_pass"
    assert report["template_only"] is True
    assert report["identity_verified"] is False
    assert report["bridge_event_written"] is False
    assert report["approval_granted"] is False
    assert report["authority"] == "none"

    event = report["event"]
    validate_event(event)
    assert list(event) == [*builder.ENVELOPE_KEY_ORDER, "role", "agent_uuid", "session_id"]
    assert event["ts_utc"] == TS_UTC
    assert event["agent"] == "claude-rco-1"
    assert event["type"] == "decision"
    assert event["status"] == "rco_pass"
    assert event["task_id"] == TASK
    assert event["severity"] == ""
    assert event["to"] == "codex-lead-1"
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["run_id"] == SESSION
    assert event["pid"] == 0
    assert event["cwd"] == builder.TEMPLATE_CWD
    assert event["role"] == "rco-security"
    assert event["agent_uuid"] == UUID_A
    assert event["session_id"] == SESSION
    assert "capabilities" not in event

    payload = event["payload"]
    assert payload["head"] == HEAD
    assert payload["exact_head"] == HEAD
    assert payload["pr"] == 1680 and type(payload["pr"]) is int
    assert payload["decision_status"] == "rco_pass"
    assert payload["summary"] == SUMMARY
    assert payload["evidence"] == list(EVIDENCE)
    assert "supersedes_event_id" not in payload
    assert payload["template"] == {
        "version": builder.TEMPLATE_VERSION,
        "kind": "rco_pass",
        "template_only": True,
        "identity_verified": False,
        "bridge_event_written": False,
        "approval_granted": False,
    }
    for key in builder.RCO_PASS_FORBIDDEN_PAYLOAD_KEYS:
        assert key not in payload

    message = event["message"]
    assert "\n" not in message and "\r" not in message
    assert HEAD in message
    assert message.startswith(f"rco_pass task={TASK} head={HEAD} pr=#1680 | ")
    assert (
        f" | {SUMMARY} | evidence: tests/tools: 191 passed; "
        "gh pr checks 1680: 6 of 6 green | "
    ) in message
    assert message.endswith("template_only=true identity_verified=false")

    writer_args = report["writer_args"]
    assert writer_args["Agent"] == "claude-rco-1"
    assert writer_args["Type"] == "decision"
    assert writer_args["Status"] == "rco_pass"
    assert writer_args["TaskId"] == TASK
    assert writer_args["To"] == "codex-lead-1"
    assert writer_args["Message"] == message
    assert writer_args["Role"] == "rco-security"
    assert writer_args["AgentUuid"] == UUID_A
    assert writer_args["SessionId"] == SESSION
    assert writer_args["RunId"] == SESSION
    assert "\n" not in writer_args["PayloadJson"]
    assert json.loads(writer_args["PayloadJson"]) == payload


@pytest.mark.parametrize("kind", builder.TEMPLATE_KINDS)
def test_every_kind_maps_to_its_closed_type_and_status(kind: str) -> None:
    agent = "claude-rco-2" if kind == "rco_pass" else "fable-5"
    report = _build(kind=kind, agent=agent, role="", agent_uuid="", session_id="")
    event = report["event"]
    validate_event(event)
    expected_type, expected_status = builder.KIND_EVENT_SHAPES[kind]
    assert (event["type"], event["status"]) == (expected_type, expected_status)
    assert event["message"].startswith(f"{kind} task={TASK} head={HEAD} pr=#1680 | ")
    payload = event["payload"]
    if kind in builder.DECISION_KINDS:
        assert payload["decision_status"] == expected_status
    else:
        assert "decision_status" not in payload
    assert report["writer_args"]["Type"] == expected_type
    assert report["writer_args"]["Status"] == expected_status


def test_optional_identity_fields_are_omitted_like_the_writer() -> None:
    report = _build(agent_uuid="", session_id="", role="", run_id="")
    event = report["event"]
    validate_event(event)
    assert list(event) == list(builder.ENVELOPE_KEY_ORDER)
    assert event["run_id"] == ""
    for key in ("role", "agent_uuid", "session_id", "capabilities"):
        assert key not in event
    writer_args = report["writer_args"]
    assert writer_args["Role"] == ""
    assert writer_args["AgentUuid"] == ""
    assert writer_args["SessionId"] == ""
    assert writer_args["RunId"] == ""


def test_progress_head_is_optional_and_message_omits_absent_fields() -> None:
    report = _build(
        kind="progress",
        agent="fable-5",
        head_sha="",
        pr=None,
        to="",
        evidence=(),
        summary="slice started",
    )
    event = report["event"]
    validate_event(event)
    payload = event["payload"]
    for key in ("head", "exact_head", "pr", "decision_status", "supersedes_event_id"):
        assert key not in payload
    assert payload["evidence"] == []
    message = event["message"]
    assert message == (
        f"progress task={TASK} | slice started | evidence: none | "
        "template_only=true identity_verified=false"
    )
    assert event["to"] == ""

    with_head = _build(kind="progress", agent="fable-5", pr=None, to="")
    assert with_head["event"]["payload"]["head"] == HEAD
    assert f" head={HEAD} | " in with_head["event"]["message"]


def test_supersedes_event_id_accepts_three_reference_forms_with_distinct_labels() -> None:
    assert builder.SUPERSEDES_REF_CANONICAL_JSON == "canonical_json_sha256"
    assert builder.SUPERSEDES_REF_RAW_LINE == "raw_line_sha256"
    assert builder.SUPERSEDES_REF_TS_UTC == "ts_utc"
    assert builder.SUPERSEDES_EFFECT == "reference_only"

    # The compact reader's event id: sha256 over canonical JSON, bare 64-hex.
    prior_event = _build(kind="progress", agent="fable-5", pr=None, to="")["event"]
    canonical = hashlib.sha256(
        json.dumps(
            prior_event, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", canonical)
    report = _build(supersedes_event_id=canonical)
    payload = report["event"]["payload"]
    assert payload["supersedes_event_id"] == canonical
    assert payload["supersedes_event_ref_kind"] == "canonical_json_sha256"
    assert payload["supersedes_effect"] == "reference_only"
    assert f" supersedes={canonical} | " in report["event"]["message"]

    raw_line = "sha256:" + "a" * 64
    payload = _build(supersedes_event_id=raw_line)["event"]["payload"]
    assert payload["supersedes_event_id"] == raw_line
    assert payload["supersedes_event_ref_kind"] == "raw_line_sha256"
    assert payload["supersedes_effect"] == "reference_only"

    for ts_form in ("2026-09-11T08:01:55.1234567Z", "2026-09-11T08:01:55Z"):
        report = _build(supersedes_event_id=ts_form)
        payload = report["event"]["payload"]
        assert payload["supersedes_event_id"] == ts_form
        assert payload["supersedes_event_ref_kind"] == "ts_utc"
        assert payload["supersedes_effect"] == "reference_only"
        assert f" supersedes={ts_form} | " in report["event"]["message"]

    absent = _build(supersedes_event_id="")["event"]["payload"]
    for key in ("supersedes_event_id", "supersedes_event_ref_kind", "supersedes_effect"):
        assert key not in absent
    assert "supersedes_event_id" not in _build(supersedes_event_id=None)["event"]["payload"]

    for rejected in (
        "yesterday",
        "2026-09-11 08:01:55",
        "2026-09-11T08:01:55+00:00",
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "SHA256:" + "a" * 64,
        "C" * 64,
        "c" * 63,
        "c" * 65,
        " " + "c" * 64,
        1726040515,
        ["c" * 64],
    ):
        assert _error(supersedes_event_id=rejected) == "supersedes_event_id_invalid"


def test_generated_at_controls_ts_utc_and_defaults_to_now() -> None:
    assert _build()["event"]["ts_utc"] == TS_UTC
    later = _build(generated_at=GENERATED_AT + timedelta(microseconds=7))
    assert later["event"]["ts_utc"] == "2026-09-11T08:30:00.000007Z"

    before = datetime.now(timezone.utc)
    event = _build(generated_at=None)["event"]
    validate_event(event)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", event["ts_utc"])
    parsed = datetime.strptime(event["ts_utc"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )
    assert before - timedelta(seconds=1) <= parsed <= datetime.now(timezone.utc) + timedelta(seconds=1)


def test_builder_is_pure_and_never_opens_files(monkeypatch: pytest.MonkeyPatch) -> None:
    source = inspect.getsource(builder)
    assert "bridge_event_writer" not in source
    assert "events.jsonl" not in source
    assert not hasattr(builder, "write_bridge_event")

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("template builder must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(os, "open", _forbidden)
    monkeypatch.setattr(Path, "open", _forbidden)
    monkeypatch.setattr(Path, "write_text", _forbidden)
    monkeypatch.setattr(Path, "write_bytes", _forbidden)
    report = _build(supersedes_event_id="sha256:" + "b" * 64)
    validate_event(report["event"])


# --- invalid input -------------------------------------------------------------


@pytest.mark.parametrize(
    "agent", ["fable-5", "codex-lead-1", "codex-tools-1", "operator", "claude-rco-3"]
)
def test_rco_pass_rejects_unrecognized_agents(agent: str) -> None:
    assert _error(agent=agent) == "rco_pass_agent_not_recognized"


def test_non_rco_kinds_accept_any_valid_agent() -> None:
    assert _build(kind="build_consensus", agent="fable-5")["event"]["agent"] == "fable-5"
    assert (
        _build(kind="changes_requested", agent="codex-tools-1")["event"]["agent"]
        == "codex-tools-1"
    )
    assert _build(kind="changes_requested", agent="claude-rco-2")["event"]["agent"] == "claude-rco-2"


@pytest.mark.parametrize(
    "kind", ["approved", "RCO_PASS", " rco_pass", "rco_pass ", "", None, 5, ["rco_pass"]]
)
def test_unknown_or_unnormalized_kind_rejected(kind: object) -> None:
    assert _error(kind=kind) == "kind_unknown"


@pytest.mark.parametrize("kind", sorted(builder.HEAD_REQUIRED_KINDS))
def test_head_sha_required_for_binding_kinds(kind: str) -> None:
    agent = "claude-rco-1" if kind == "rco_pass" else "fable-5"
    assert _error(kind=kind, agent=agent, head_sha="") == "head_sha_required"
    assert _error(kind=kind, agent=agent, head_sha=None) == "head_sha_required"


@pytest.mark.parametrize(
    "head_sha",
    [HEAD[:8], HEAD[:12], HEAD.upper(), HEAD + "0", HEAD[:-1] + "g", " " + HEAD, HEAD + "\n", 12345],
)
def test_head_sha_must_be_full_lowercase_40_hex(head_sha: object) -> None:
    assert _error(head_sha=head_sha) == "head_sha_invalid"
    assert _error(kind="progress", agent="fable-5", head_sha=head_sha) == "head_sha_invalid"


def test_review_requested_requires_targets_and_normalizes_them() -> None:
    assert _error(kind="review_requested", agent="fable-5", to="") == "to_required"
    assert _error(kind="review_requested", agent="fable-5", to="  ,  ") == "to_required"
    normalized = _build(
        kind="review_requested", agent="fable-5", to=" claude-rco-1 , claude-rco-2 "
    )
    assert normalized["event"]["to"] == "claude-rco-1,claude-rco-2"
    assert normalized["writer_args"]["To"] == "claude-rco-1,claude-rco-2"
    assert _build(kind="progress", agent="fable-5", to="github/main")["event"]["to"] == "github/main"


@pytest.mark.parametrize(
    "to", ["claude rco", "Claude-RCO-1", "claude-rco-1,", "codex-lead-1,,claude-rco-1", ["claude-rco-1"], "a"]
)
def test_invalid_targets_rejected(to: object) -> None:
    assert _error(to=to) == "to_invalid"


@pytest.mark.parametrize(
    "task_id",
    ["fable-5/bridge-message-template-20260911", "wd.ops-1", "a" * 180, "Task_9/sub.part-2"],
)
def test_task_id_accepts_writer_binding_shapes(task_id: str) -> None:
    assert _build(task_id=task_id)["event"]["task_id"] == task_id


@pytest.mark.parametrize(
    "task_id",
    ["", "   ", "a/../b", "a//b", "/lead", "lead/", "a\\b", "a:b", "a" * 181, "tab\tid", "sp ace", 42, None],
)
def test_task_id_rejects_unsafe_shapes(task_id: object) -> None:
    assert _error(task_id=task_id) == "task_id_invalid"


def test_summary_bounds() -> None:
    assert builder.MAX_SUMMARY_CHARS == 400
    assert _error(summary="") == "summary_empty"
    assert _error(summary="   ") == "summary_empty"
    assert _error(summary=None) == "summary_invalid"
    assert _error(summary=["ok"]) == "summary_invalid"
    assert _error(summary="x" * 401) == "summary_too_long"
    assert _build(summary="x" * 400)["event"]["payload"]["summary"] == "x" * 400
    assert _error(summary="line1\nline2") == "summary_multiline"
    assert _error(summary="line1\rline2") == "summary_multiline"
    assert _error(summary="rivi1\u2028rivi2") == "summary_multiline"
    assert _error(summary="rivi1\x85rivi2") == "summary_multiline"
    assert _error(summary="a | b") == "summary_reserved_char"
    assert _error(summary="a; b") == "summary_reserved_char"
    assert _error(summary="ctrl\x07") == "summary_control_char"
    assert _error(summary="tab\there") == "summary_control_char"
    assert _error(summary="c1\x9fctrl") == "summary_control_char"
    assert _error(summary="zero\u200bwidth") == "summary_control_char"
    assert _error(summary="bidi\u202eoverride") == "summary_control_char"
    assert _error(summary="see synthetic_secret_DO_NOT_LEAK note") == "private_marker"
    assert _error(summary="see private_marker note") == "private_marker"
    assert _build(summary="  padded  ")["event"]["payload"]["summary"] == "padded"


def test_unicode_text_is_accepted_and_kept_unescaped() -> None:
    summary = "kaikki 86 testiä läpi, ei löydöksiä ✅"
    evidence = ["testit: 86 läpi", "käännös: 日本語 ok"]
    report = _build(summary=summary, evidence=evidence)
    payload = report["event"]["payload"]
    assert payload["summary"] == summary
    assert payload["evidence"] == evidence
    message = report["event"]["message"]
    assert f" | {summary} | evidence: testit: 86 läpi; käännös: 日本語 ok | " in message
    validate_event(report["event"])
    payload_json = report["writer_args"]["PayloadJson"]
    assert "läpi" in payload_json and "\\u00e4" not in payload_json
    assert json.loads(payload_json) == payload
    assert _build(summary="ä" * 400)["event"]["payload"]["summary"] == "ä" * 400
    assert _error(summary="ä" * 401) == "summary_too_long"


def test_evidence_bounds() -> None:
    assert builder.MAX_EVIDENCE_ITEMS == 12
    assert builder.MAX_EVIDENCE_CHARS == 200
    assert _error(evidence="single string") == "evidence_invalid"
    assert _error(evidence=None) == "evidence_invalid"
    assert _error(evidence=[1]) == "evidence_item_invalid"
    assert _error(evidence=[""]) == "evidence_item_empty"
    assert _error(evidence=["   "]) == "evidence_item_empty"
    assert _error(evidence=["x" * 201]) == "evidence_item_too_long"
    assert _build(evidence=["x" * 200])["event"]["payload"]["evidence"] == ["x" * 200]
    assert _error(evidence=["a\nb"]) == "evidence_item_multiline"
    assert _error(evidence=["a\u2029b"]) == "evidence_item_multiline"
    assert _error(evidence=["a|b"]) == "evidence_item_reserved_char"
    assert _error(evidence=["a;b"]) == "evidence_item_reserved_char"
    assert _build(evidence=["café"])["event"]["payload"]["evidence"] == ["café"]
    assert _error(evidence=["a\x1bb"]) == "evidence_item_control_char"
    assert _error(evidence=["a\u200db"]) == "evidence_item_control_char"
    assert _error(evidence=["ref synthetic_secret_DO_NOT_LEAK"]) == "private_marker"
    assert _error(evidence=[f"e{i}" for i in range(13)]) == "evidence_too_many"
    twelve = [f"e{i}" for i in range(12)]
    assert _build(evidence=twelve)["event"]["payload"]["evidence"] == twelve
    assert _build(evidence=["  padded ref  "])["event"]["payload"]["evidence"] == ["padded ref"]


def test_identity_fields_validated() -> None:
    assert _error(agent="Claude-RCO-1") == "agent_invalid"
    assert _error(agent="") == "agent_invalid"
    assert _error(agent=None) == "agent_invalid"
    assert _error(agent="claude rco 1") == "agent_invalid"
    assert _error(agent_uuid="not-a-uuid") == "agent_uuid_invalid"
    assert _error(agent_uuid=UUID_A.replace("-", "")) == "agent_uuid_invalid"
    assert _error(agent_uuid=5) == "agent_uuid_invalid"
    assert _error(session_id="bad session") == "session_id_invalid"
    assert _error(session_id="s" * 129) == "session_id_invalid"
    assert _error(role="Bad Role") == "role_invalid"
    assert _error(role="r") == "role_invalid"
    assert _error(run_id="bad run") == "run_id_invalid"
    assert _error(run_id=7) == "run_id_invalid"


@pytest.mark.parametrize("pr", ["1680", 0, -1, True, False, 1.0])
def test_pr_must_be_a_positive_plain_int(pr: object) -> None:
    assert _error(pr=pr) == "pr_invalid"


def test_pr_none_omits_the_field() -> None:
    report = _build(pr=None)
    assert "pr" not in report["event"]["payload"]
    assert " pr=#" not in report["event"]["message"]


@pytest.mark.parametrize(
    "generated_at",
    [
        datetime(2026, 9, 11, 8, 30, 0),
        datetime(2026, 9, 11, 8, 30, 0, tzinfo=timezone(timedelta(hours=3))),
        "2026-09-11T08:30:00Z",
        1726040515,
    ],
)
def test_generated_at_must_be_an_aware_utc_datetime(generated_at: object) -> None:
    assert _error(generated_at=generated_at) == "generated_at_invalid"


# --- CLI -----------------------------------------------------------------------


def test_cli_success_emits_validated_json_report() -> None:
    completed = _run_cli(
        "--kind",
        "review_requested",
        "--task-id",
        TASK,
        "--agent",
        "fable-5",
        "--head-sha",
        HEAD,
        "--to",
        "claude-rco-1",
        "--pr",
        "1680",
        "--summary",
        "PR #1680 ready for review at exact head",
        "--evidence",
        "tests/tools: 41 passed",
        "--evidence",
        "CI: pending",
        "--agent-uuid",
        UUID_A,
        "--session-id",
        SESSION,
        "--role",
        "fable-producer",
        "--generated-at",
        "2026-09-11T08:30:00Z",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    event = report["event"]
    validate_event(event)
    assert report["template_only"] is True
    assert report["bridge_event_written"] is False
    assert event["type"] == "wake_request"
    assert event["status"] == "review_requested"
    assert event["to"] == "claude-rco-1"
    assert event["ts_utc"] == TS_UTC
    assert event["agent_uuid"] == UUID_A
    assert event["session_id"] == SESSION
    assert event["role"] == "fable-producer"
    assert event["payload"]["pr"] == 1680
    assert event["payload"]["evidence"] == ["tests/tools: 41 passed", "CI: pending"]
    assert json.loads(report["writer_args"]["PayloadJson"]) == event["payload"]


def test_cli_writer_args_only_prints_only_dispatch_values() -> None:
    base_args = (
        "--kind",
        "build_consensus",
        "--task-id",
        TASK,
        "--agent",
        "codex-tools-1",
        "--head-sha",
        HEAD,
        "--pr",
        "1680",
        "--summary",
        "fokusoidut testit läpi: 136 passed",
        "--evidence",
        "receipt: .codex-audit/tools-live-continuity-fix-20260911/receipt.md",
        "--generated-at",
        "2026-09-11T08:30:00Z",
    )
    full = _run_cli(*base_args)
    assert full.returncode == 0, full.stderr
    report = json.loads(full.stdout)
    assert "ä" in full.stdout and "\\u00e4" not in full.stdout

    only = _run_cli(*base_args, "--writer-args-only")
    assert only.returncode == 0, only.stderr
    assert only.stderr == ""
    writer_args = json.loads(only.stdout)
    assert writer_args == report["writer_args"]
    assert set(writer_args) == {
        "Agent",
        "Type",
        "Status",
        "TaskId",
        "To",
        "Message",
        "Role",
        "AgentUuid",
        "SessionId",
        "RunId",
        "PayloadJson",
    }
    assert "event" not in writer_args and "template_version" not in writer_args
    assert writer_args["Status"] == "build_consensus_pass"
    assert json.loads(writer_args["PayloadJson"])["exact_head"] == HEAD
    assert only.stdout.count("\n") > 1

    compact = _run_cli(*base_args, "--writer-args-only", "--compact")
    assert compact.returncode == 0, compact.stderr
    assert compact.stdout.count("\n") == 1
    assert json.loads(compact.stdout) == writer_args


def test_cli_compact_flag_emits_single_line_json() -> None:
    completed = _run_cli(
        "--kind",
        "progress",
        "--task-id",
        TASK,
        "--agent",
        "fable-5",
        "--summary",
        "slice started",
        "--generated-at",
        "2026-09-11T08:30:00.250000Z",
        "--compact",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("\n") == 1
    report = json.loads(completed.stdout)
    assert report["event"]["ts_utc"] == "2026-09-11T08:30:00.250000Z"
    assert report["event"]["type"] == "status"


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (
            ["--kind", "rco_pass", "--task-id", TASK, "--agent", "fable-5", "--head-sha", HEAD, "--summary", "s"],
            "rco_pass_agent_not_recognized",
        ),
        (
            ["--kind", "build_consensus", "--task-id", TASK, "--agent", "codex-tools-1", "--summary", "s"],
            "head_sha_required",
        ),
        (
            ["--kind", "review_requested", "--task-id", TASK, "--agent", "fable-5", "--head-sha", HEAD, "--summary", "s"],
            "to_required",
        ),
        (
            [
                "--kind", "progress", "--task-id", TASK, "--agent", "fable-5", "--summary", "s",
                "--generated-at", "2026-09-11T08:30:00+03:00",
            ],
            "generated_at_invalid",
        ),
        (
            ["--kind", "progress", "--task-id", TASK, "--agent", "fable-5", "--summary", "s", "--generated-at", "now"],
            "generated_at_invalid",
        ),
        (
            ["--kind", "progress", "--task-id", TASK, "--agent", "fable-5", "--summary", "s", "--pr", "0"],
            "pr_invalid",
        ),
    ],
)
def test_cli_invalid_input_exits_2_with_reason_json(args: list[str], reason: str) -> None:
    completed = _run_cli(*args)
    assert completed.returncode == 2
    assert completed.stdout == ""
    error = json.loads(completed.stderr)
    assert error == {
        "error": reason,
        "template_only": True,
        "bridge_event_written": False,
    }


def test_cli_unknown_kind_is_an_argparse_error() -> None:
    completed = _run_cli(
        "--kind", "approved", "--task-id", TASK, "--agent", "fable-5", "--summary", "s"
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "invalid choice" in completed.stderr
