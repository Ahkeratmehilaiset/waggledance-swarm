"""Actual C3/C2/resolver and synthetic in-process reader; no native launch."""
import datetime as dt
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools import wd_q4_public_query as query
from tests.tools.test_wd_q4_card_snapshot_pointer import call, staged
from tests.tools import test_shared_card_route as rt


class Unreadable:
    def read(self, *args):
        pytest.fail("invalid binding consumed stdin")


@pytest.fixture
def route(call, monkeypatch):
    from sandbox.mind.step2 import runtime_launcher
    from sandbox.mind.step2 import wd_q4_private_read as reader
    pointer = json.loads(call["pointer_bytes"])
    directory = call["root"] / pointer["generation"]
    descriptor = json.loads((directory / "descriptor.json").read_bytes())
    config = rt.real_config(directory, pointer["artifacts"]["descriptor.json"])
    args = dict(call, expected_source_sha256=pointer["source_prefix"]["sha256"],
                expected_encoder_sha256=query._encoder_digest(descriptor["encoder"]))
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kwargs: rt.fx.Encoder())
    monkeypatch.setattr(reader, "bind_runtime_encoder", lambda *args: None)
    monkeypatch.setattr(query.q4_query, "_utc_now", lambda: dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc))
    calls = []

    def launcher(*, query, **kwargs):
        calls.append(True)
        situation = json.loads(query)
        out = reader._private_read_locked(directory, directory / "descriptor.json",
                    config.descriptor_sha256, situation["situation"], situation["time"],
                    r"C:\synthetic\model", config.runtime_revision)
        return runtime_launcher._validate_output(json.dumps(out).encode(), 0, config.descriptor_sha256)

    monkeypatch.setattr(query.q4_query, "_invoke_launcher", launcher)
    return dict(config=config, args=args, calls=calls, directory=directory, launcher=launcher)


def run(route, stdin=None):
    if stdin is None:
        stdin = io.BytesIO(json.dumps(dict(situation="blocked red mind genesis needs operator disk",
                                          time="2026-11-02T00:00:00Z")).encode())
    return query.query_staged_card_snapshot(route["config"], stdin, **route["args"])


def test_default_off_before_config_stdin_or_root():
    with pytest.raises(query.PublicQueryRefused, match="^disabled$"):
        query.query_staged_card_snapshot(object(), Unreadable(), root=object())


def test_a_host_native_posix_path_is_refused_by_the_binding_rule_before_stdin_root_or_pointer(monkeypatch):
    """Platform-independent contract: the staged route is Windows-only. A POSIX path in the config tuple is refused by
    q4._validate_config (invalid_binding) with every pin well-formed and before stdin, root or pointer are touched;
    the spy proves the refusal comes from that check and not from a later compare."""
    seen = []
    original = query.q4_query._validate_config

    def spy(config):
        try:
            return original(config)
        except query.q4_query.QueryRefused as exc:
            seen.append(str(exc))
            raise

    monkeypatch.setattr(query.q4_query, "_validate_config", spy)
    config = replace(rt.config(Path(rt.SYNTHETIC_MEMORY), "7" * 64),
                     memory="/tmp/cards", descriptor="/tmp/cards/descriptor.json")
    pins = dict(expected_pointer_sha256="1" * 64, expected_source_sha256="2" * 64, expected_encoder_sha256="3" * 64,
                policy_sha256="4" * 64, roster_sha256="5" * 64, card_decision_sha256="6" * 64)
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        query.query_staged_card_snapshot(config, Unreadable(), root=object(), enabled=True, **pins)
    assert seen == ["invalid_binding"]


def test_actual_c3_c2_snapshot_query_answers_without_v6_history(route, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("staged query used v6 history")
    monkeypatch.setattr(query.pointer_resolver.checkpoint, "read_verified_pointer", forbidden)
    before = {str(p): p.read_bytes() for p in route["args"]["root"].rglob("*") if p.is_file()}
    out = run(route)
    assert out["schema"] == "wd.q4-card-snapshot-query.v1" and out["status"] == "ANSWER"
    assert route["calls"] == [True]
    assert out["binding"]["source_mode"] == "card-snapshot-v1"
    assert out["binding"]["source_anchor_sha256"] == route["args"]["expected_source_sha256"]
    assert out["binding"]["card_decision_sha256"] == route["args"]["card_decision_sha256"]
    assert not ({"receipt_sha256", "history", "watermark_sha256"} & out["binding"].keys())
    projection = json.loads((route["directory"] / "projection.json").read_bytes())
    texts = {r["stored_text"] for r in projection["records"]}
    assert out["results"] and all(r["stored_text"] in texts for r in out["results"])
    assert out["freshness"] == out["publisher_authentication"] == "UNKNOWN"
    assert before == {str(p): p.read_bytes() for p in route["args"]["root"].rglob("*") if p.is_file()}


@pytest.mark.parametrize("field", ["expected_source_sha256", "expected_encoder_sha256",
                                   "expected_pointer_sha256", "card_decision_sha256",
                                   "policy_sha256", "roster_sha256"])
def test_wrong_pin_refuses_before_query_or_stdin(route, field):
    route["args"][field] = "0" * 64
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        run(route, Unreadable())
    assert route["calls"] == []


@pytest.mark.parametrize("field", ["source_path", "generation_path", "end_cursor", "card_policy_bytes",
                                   "card_decision_bytes", "pointer_bytes"])
def test_missing_evidence_refuses_before_query(route, field):
    route["args"][field] = None
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        run(route, Unreadable())
    assert route["calls"] == []


@pytest.mark.parametrize("field", ["memory", "descriptor", "descriptor_sha256", "runtime_revision"])
def test_config_tuple_mismatch_refuses_before_query(route, field):
    value = "0" * 64 if field.endswith("sha256") else (
            "0" * 40 if field == "runtime_revision" else r"C:\synthetic\other")
    route["config"] = replace(route["config"], **{field: value})
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        run(route, Unreadable())
    assert route["calls"] == []


def test_source_change_during_query_refuses_result(route, monkeypatch):
    def launcher(**kwargs):
        out = route["launcher"](**kwargs)
        source = Path(route["args"]["source_path"])
        source.write_bytes(b"!" + source.read_bytes()[1:])
        return out
    monkeypatch.setattr(query.q4_query, "_invoke_launcher", launcher)
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        run(route)
    assert route["calls"] == [True]


@pytest.mark.parametrize("bad", [None, {}, {"status": "UNKNOWN", "results": []},
                                  {"status": "ANSWER", "results": []},
                                  {"status": "ABSTAIN", "results": [{}]},
                                  {"status": "ANSWER", "results": [{"stored_text": "unexpected"}]}])
def test_malformed_query_result_is_never_returned(route, monkeypatch, bad):
    monkeypatch.setattr(query.q4_query, "query_and_resolve", lambda *a, **kw: bad)
    with pytest.raises(query.PublicQueryRefused, match="^staged_query_refused$"):
        run(route)


def test_prior_query_time_abstains_with_actual_resolver(route):
    out = run(route, io.BytesIO(json.dumps(dict(situation="mind genesis", time="2026-10-01T00:00:00Z")).encode()))
    assert out["status"] == "ABSTAIN" and out["results"] == []
