"""RED-first contract: a caller-controlled UTC cutoff for the fenced catch-up of the Q4 prefix build child.

Synthetic only, tests only (no Source change in this file's task). The producer is replaced by in-test fakes, the
backend by the fake module of ``test_wd_q4_prefix_build_child``; no bridge data, model or production run.

Expected child change (not implemented here): ``build_prefix_generation`` gains a keyword-only ``cutoff_utc``
(default ``None``). The child never reads a clock: the cutoff is a caller assertion, an aware ``datetime``. Only a
fenced v3 plan forwards it, as the same object, to ``producer.publish_public_catchup(cutoff_utc=...)``; any other
value, or a cutoff on a non-fenced plan, refuses ``request_refused`` before identity, backend or producer. Without
a cutoff every route calls the producer exactly as today. With a cutoff the closed report also admits
``coverage == "partial_ready_prefix"`` for published windows and a closed empty result (``EMPTY_NO_CHANGE`` /
``EMPTY_ADVANCED``, generation ``None``, never at genesis). Assumption, to be aligned with the producer's
separate empty-prefix correction: an empty ready prefix reports ``coverage == "partial_ready_prefix"``.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import inspect

import pytest

from sandbox.mind.step2 import shared_prefix_public_producer as producer
from sandbox.mind.step2.test_wd_q4_prefix_build_child import (  # noqa: F401 - fixtures used by name
    SENTINEL, backend, enc, model_dir, module, never_loads, nothing_written, plan, refused, roots, run, sha,
    synthetic_config)

CUTOFF = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
CAUGHT_UP = "caught_up_to_final_snapshot_not_history"
PARTIAL = "partial_ready_prefix"
CHILD_KEYS = {"encoder_identity_source", "plan_sha256", "backend_isolation", "model_assets"}
CATCHUP_KEYS = {"status", "pointer_sha256", "generation", "publisher_authentication", "source_authenticity",
                "freshness", "coverage", "source_refusals"}
PRIOR = "2" * 64


@dataclass(frozen=True, slots=True)
class FakeFencedRequest(producer.PrefixPublicRequest):
    fenced: bool = False


def report(status="PUBLISHED_GENESIS", pointer="1" * 64, generation="gen-a", coverage=CAUGHT_UP, **extra):
    return {"status": status, "pointer_sha256": pointer, "generation": generation,
            "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
            "coverage": coverage, "source_refusals": "NOT_AGGREGATED", **extra}


def fenced_world(tmp_path, model_dir, monkeypatch, *, pointer=None, schema="wd.q4-prefix-build-plan.v3"):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    extra = {"max_windows": 4}
    if schema == "wd.q4-prefix-build-plan.v3":
        extra["fenced"] = True
        monkeypatch.setattr(m.producer, "PrefixPublicRequest", FakeFencedRequest)
    elif schema == "wd.q4-prefix-build-plan.v1":
        extra = {}
    doc = plan(model_dir, pins, schema=schema, pointer=pointer, **extra)
    return m, doc, config, start


def call(m, doc, config, start, tmp_path, **kwargs):
    root, private_root = roots(tmp_path)
    raw = enc(doc)
    return m.build_prefix_generation(enabled=True, plan_bytes=raw, expected_plan_sha256=sha(raw), config=config,
                                     start_prefix=start, root=root, private_state_root=private_root,
                                     trusted_model_root=model_dir_of(doc), **kwargs)


def model_dir_of(doc):
    from pathlib import Path
    return Path(doc["encoder"]["model_dir"])


def catchup_fake(monkeypatch, m, answer, seen):
    def publish(*, enabled, request, max_windows, cutoff_utc):
        seen.update(enabled=enabled, fenced=request.fenced, max_windows=max_windows, cutoff_utc=cutoff_utc)
        return answer

    monkeypatch.setattr(m.producer, "publish_public_prefix", lambda **kw: pytest.fail("wrong route"))
    monkeypatch.setattr(m.producer, "publish_public_catchup", publish)


# 1 propagation ---------------------------------------------------------------------------------------------------

def test_the_child_offers_an_optional_keyword_only_cutoff():
    parameter = inspect.signature(module().build_prefix_generation).parameters.get("cutoff_utc")
    assert parameter is not None and parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is None


@pytest.mark.parametrize("cutoff", [CUTOFF, CUTOFF.astimezone(timezone(timedelta(hours=2)))])
def test_a_fenced_cutoff_reaches_the_producer_as_the_same_object(tmp_path, model_dir, backend, monkeypatch, cutoff):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch)
    seen = {}
    catchup_fake(monkeypatch, m, report(), seen)
    result = call(m, doc, config, start, tmp_path, cutoff_utc=cutoff)
    assert seen["cutoff_utc"] is cutoff
    assert {k: seen[k] for k in ("enabled", "fenced", "max_windows")} == {"enabled": True, "fenced": True,
                                                                         "max_windows": 4}
    assert result["coverage"] == CAUGHT_UP and result["plan_sha256"] == sha(enc(doc))


# 2 closed partial / empty reporting ------------------------------------------------------------------------------

@pytest.mark.parametrize("status,pointer,prior,generation", [
    ("PUBLISHED_GENESIS", "1" * 64, None, "gen-a"),
    ("PUBLISHED_HISTORY", "1" * 64, PRIOR, "gen-a"),
])
def test_a_partial_ready_prefix_is_reported_closed_under_a_cutoff(tmp_path, model_dir, backend, monkeypatch, status,
                                                                  pointer, prior, generation):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch, pointer=prior)
    catchup_fake(monkeypatch, m, report(status, pointer, generation, PARTIAL), {})
    result = call(m, doc, config, start, tmp_path, cutoff_utc=CUTOFF)
    assert set(result) == CATCHUP_KEYS | CHILD_KEYS
    assert result["status"] == status and result["coverage"] == PARTIAL
    assert result["freshness"] == "UNKNOWN" and result["source_refusals"] == "NOT_AGGREGATED"


@pytest.mark.parametrize("status,pointer", [("EMPTY_NO_CHANGE", PRIOR), ("EMPTY_ADVANCED", "3" * 64)])
def test_an_empty_ready_prefix_is_reported_closed_under_a_cutoff(tmp_path, model_dir, backend, monkeypatch, status,
                                                                 pointer):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch, pointer=PRIOR)
    catchup_fake(monkeypatch, m, report(status, pointer, None, PARTIAL), {})
    result = call(m, doc, config, start, tmp_path, cutoff_utc=CUTOFF)
    assert result["status"] == status and result["generation"] is None and result["coverage"] == PARTIAL


@pytest.mark.parametrize("answer", [
    report("EMPTY_ADVANCED", "3" * 64, None, PARTIAL),  # empty at genesis
    report("EMPTY_NO_CHANGE", "3" * 64, None, PARTIAL),
    report("PUBLISHED_GENESIS", coverage="partial"),
    report("PUBLISHED_GENESIS", coverage="caught_up"),
    report("PUBLISHED_GENESIS", coverage=""),
    report("PUBLISHED_GENESIS", coverage=PARTIAL, end_reason="deferred"),
    report("PUBLISHED_GENESIS", coverage=PARTIAL, cutoff_utc=CUTOFF.isoformat()),
    report("PUBLISHED_GENESIS", coverage=PARTIAL, records=[{"message": SENTINEL}]),
    dict(report("PUBLISHED_GENESIS", coverage=PARTIAL), source_refusals=0),
    dict(report("PUBLISHED_GENESIS", coverage=PARTIAL), freshness="CURRENT"),
], ids=["empty-genesis-advanced", "empty-genesis-no-change", "open-partial", "open-caught-up", "blank",
        "extra-end-reason", "extra-cutoff", "extra-records", "counted-refusals", "fresh-claim"])
def test_an_open_or_widened_report_under_a_cutoff_is_unknown_state(tmp_path, model_dir, backend, monkeypatch,
                                                                    answer):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch)
    catchup_fake(monkeypatch, m, answer, {})
    error = refused("publication_state_unknown", lambda: call(m, doc, config, start, tmp_path, cutoff_utc=CUTOFF))
    assert SENTINEL not in repr(error)


@pytest.mark.parametrize("status,pointer", [("EMPTY_NO_CHANGE", PRIOR), ("EMPTY_ADVANCED", "3" * 64)])
def test_an_empty_advance_claiming_full_coverage_under_a_cutoff_is_unknown_state(tmp_path, model_dir, backend,
                                                                                 monkeypatch, status, pointer):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch, pointer=PRIOR)
    catchup_fake(monkeypatch, m, report(status, pointer, None, CAUGHT_UP), {})
    refused("publication_state_unknown", lambda: call(m, doc, config, start, tmp_path, cutoff_utc=CUTOFF))


def test_without_a_cutoff_a_partial_report_stays_unknown_state(tmp_path, model_dir, backend, monkeypatch):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch)
    monkeypatch.setattr(m.producer, "publish_public_catchup",
                        lambda *, enabled, request, max_windows: report(coverage=PARTIAL))
    refused("publication_state_unknown", lambda: run(m, doc, config, start, *roots(tmp_path)))


# 3 refusals before model and producer -----------------------------------------------------------------------------

@pytest.mark.parametrize("cutoff", [datetime(2026, 11, 2, 0, 0, 0), "2026-11-02T00:00:00+00:00", 0, True,
                                    date(2026, 11, 2), CUTOFF.timestamp()],
                         ids=["naive", "string", "zero", "bool", "date", "epoch"])
def test_an_invalid_cutoff_refuses_before_identity_backend_or_producer(tmp_path, model_dir, monkeypatch, cutoff):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch)
    never_loads(m, monkeypatch)
    monkeypatch.setattr(m.producer, "publish_public_catchup", lambda **kw: pytest.fail("producer entered"))
    refused("request_refused", lambda: call(m, doc, config, start, tmp_path, cutoff_utc=cutoff))
    nothing_written(*roots(tmp_path))


@pytest.mark.parametrize("schema", ["wd.q4-prefix-build-plan.v1", "wd.q4-prefix-build-plan.v2"])
def test_a_cutoff_on_a_non_fenced_plan_refuses_before_identity_backend_or_producer(tmp_path, model_dir, monkeypatch,
                                                                                    schema):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch, schema=schema)
    never_loads(m, monkeypatch)
    monkeypatch.setattr(m.producer, "publish_public_catchup", lambda **kw: pytest.fail("producer entered"))
    monkeypatch.setattr(m.producer, "publish_public_prefix", lambda **kw: pytest.fail("producer entered"))
    refused("request_refused", lambda: call(m, doc, config, start, tmp_path, cutoff_utc=CUTOFF))
    nothing_written(*roots(tmp_path))


# 4 legacy calls unchanged without a cutoff ------------------------------------------------------------------------

@pytest.mark.parametrize("schema", ["wd.q4-prefix-build-plan.v1", "wd.q4-prefix-build-plan.v2",
                                    "wd.q4-prefix-build-plan.v3"])
def test_without_a_cutoff_the_producer_call_is_unchanged(tmp_path, model_dir, backend, monkeypatch, schema):
    m, doc, config, start = fenced_world(tmp_path, model_dir, monkeypatch, schema=schema)
    seen = {}
    if schema == "wd.q4-prefix-build-plan.v1":
        def prefix(*, enabled, request):  # no cutoff keyword accepted
            seen["route"] = "prefix"
            return {key: value for key, value in report().items() if key not in ("coverage", "source_refusals")}
        monkeypatch.setattr(m.producer, "publish_public_prefix", prefix)
        monkeypatch.setattr(m.producer, "publish_public_catchup", lambda **kw: pytest.fail("wrong route"))
    else:
        def catchup(*, enabled, request, max_windows):  # no cutoff keyword accepted
            seen["route"] = "catchup"
            return report()
        monkeypatch.setattr(m.producer, "publish_public_catchup", catchup)
        monkeypatch.setattr(m.producer, "publish_public_prefix", lambda **kw: pytest.fail("wrong route"))
    result = run(m, doc, config, start, *roots(tmp_path))
    assert seen["route"] == ("prefix" if schema.endswith("v1") else "catchup")
    assert result["status"] == "PUBLISHED_GENESIS"
