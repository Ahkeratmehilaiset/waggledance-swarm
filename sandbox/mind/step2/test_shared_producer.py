"""Synthetic staging only: no scheduler, real event source, or model."""
import hashlib
import json
from dataclasses import asdict, replace

import pytest

from sandbox.mind.step2 import shared_producer as producer
from sandbox.mind.step2.test_shared_source_anchor import (
    GENERATION, TASKS, _bytes, _line, _pins,
)
from waggledance.core.bridge_log_reader import read_bridge_log


@pytest.fixture
def config(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    source.write_bytes(b"")
    generation = tmp_path / "generation.json"
    generation.write_bytes(_bytes({"generation": GENERATION}))
    cursor = read_bridge_log(source, generation_path=generation).candidate_cursor
    pins = _pins()
    anchor = _bytes({"schema": "wd.q4-source-anchor.v1", "source_path": str(source),
                     "generation_path": str(generation), "cursor_start": asdict(cursor),
                     "policy_sha256": pins["policy_sha256"], "roster_sha256": pins["roster_sha256"]})
    source.write_bytes(_line("done"))
    return producer.SourceConfig(source=source, generation_path=generation, cursor=cursor,
        anchor_bytes=anchor, anchor_sha256=hashlib.sha256(anchor).hexdigest(),
        max_bytes=4096, max_rows=20, **pins)


def test_default_off_never_reads_or_builds(monkeypatch):
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", lambda *a, **k: pytest.fail("read"))
    assert producer.prepare_delta() == {"status": "OFF"}


def test_synthetic_anchored_delta_is_staged_not_published(config):
    prepared = producer.prepare_delta(enabled=True, config=config)
    assert prepared.status == "PREPARED_NOT_PUBLISHED"
    assert prepared.result["projection"]["accepted"] == 1
    assert prepared.result["candidate_cursor"].offset == len(config.source.read_bytes())
    assert "raw-private" not in repr(prepared)
    with pytest.raises(producer.ProducerRefused, match="^history_checkpoint_integration_incomplete$"):
        producer.publish_prepared(prepared)


@pytest.mark.parametrize("change", [
    {"anchor_sha256": "0" * 64}, {"policy_sha256": "0" * 64},
    {"roster_sha256": "0" * 64}, {"max_bytes": 0}, {"max_rows": True},
])
def test_bad_config_refuses_without_publication(config, change):
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_delta(enabled=True, config=replace(config, **change))


def test_no_implicit_enable_or_non_boolean_enable(config):
    for enabled in (1, "true", None):
        with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
            producer.prepare_delta(enabled=enabled, config=config)


def test_source_substitution_and_spliced_projection_refused(config, monkeypatch):
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_delta(enabled=True, config=replace(config, source=config.generation_path))
    original = producer.snapshot.read_anchored_delta
    def splice(*args, **kwargs):
        result = original(*args, **kwargs)
        result["projection"]["records"] = []
        return result
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", splice)
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_delta(enabled=True, config=config)


def test_exception_content_is_not_exposed(config, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ValueError("synthetic-secret")
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", fail)
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$") as err:
        producer.prepare_delta(enabled=True, config=config)
    assert "synthetic-secret" not in str(err.value)
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def public_config(config):
    policy = _bytes({"schema": "wd.mind-public-status-policy.v1", "public_tasks": sorted(TASKS),
                     "public_types": ["message"]})
    policy_sha = hashlib.sha256(policy).hexdigest()
    anchor = json.loads(config.anchor_bytes)
    anchor.update(status_mode="public-v1", public_policy_sha256=policy_sha)
    anchor_bytes = _bytes(anchor)
    source = replace(config, anchor_bytes=anchor_bytes, anchor_sha256=hashlib.sha256(anchor_bytes).hexdigest())
    event = json.loads(_line("done"))
    event.update(to="", payload={"public_status": True}, message="")
    private = dict(event, to="fable-5", message="synthetic-private-marker")
    source.source.write_bytes(_bytes(event) + b"\n" + _bytes(private) + b"\n")
    return producer.PublicSourceConfig(source=source, public_policy_bytes=policy, public_policy_sha256=policy_sha)


def test_public_prepare_off_never_reads(monkeypatch):
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", lambda *a, **k: pytest.fail("read"))
    assert producer.prepare_public_delta() == {"status": "OFF"}


def test_configs_do_not_expose_paths_or_cursor_in_repr(config, public_config):
    for value in (config, public_config):
        text = repr(value)
        assert repr(config.source) not in text
        assert repr(config.generation_path) not in text
        assert config.cursor.file_identity not in text


def test_public_prepare_passes_real_metadata_builder(public_config):
    from sandbox.mind.step2 import status_metadata_producer
    prepared = producer.prepare_public_delta(enabled=True, config=public_config)
    source = public_config.source
    data = status_metadata_producer.build_status(prepared.result, enabled=True, anchor_bytes=source.anchor_bytes,
        anchor_sha256=source.anchor_sha256, policy_bytes=source.policy_bytes, policy_sha256=source.policy_sha256,
        roster_bytes=source.roster_bytes, roster_sha256=source.roster_sha256,
        public_policy_bytes=public_config.public_policy_bytes, public_policy_sha256=public_config.public_policy_sha256)
    assert prepared.status == "PREPARED_NOT_PUBLISHED"
    assert len(json.loads(data)["records"]) == 1
    assert b"synthetic-private-marker" not in data


@pytest.mark.parametrize("field", ["anchor_sha256", "policy_sha256", "roster_sha256"])
def test_public_prepare_rejects_wrong_source_pins(public_config, field):
    invalid = replace(public_config, source=replace(public_config.source, **{field: "0" * 64}))
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=invalid)


def test_public_prepare_rejects_wrong_public_pin_and_spliced_result(public_config, monkeypatch):
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=replace(public_config, public_policy_sha256="0" * 64))
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=replace(public_config, public_policy_bytes=b"{}"))
    original = producer.snapshot.read_anchored_delta
    def splice(*args, **kwargs):
        result = original(*args, **kwargs)
        result["projection"]["records"] = []
        return result
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", splice)
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=public_config)


def test_public_prepare_rejects_legacy_or_non_boolean_enable(config, public_config):
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=replace(public_config, source=config))
    for enabled in (1, "true", None):
        with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
            producer.prepare_public_delta(enabled=enabled, config=public_config)


def test_public_prepare_rejects_legacy_result_splice(config, public_config, monkeypatch):
    legacy = producer.prepare_delta(enabled=True, config=config)
    monkeypatch.setattr(producer.snapshot, "read_anchored_delta", lambda *args, **kwargs: legacy.result)
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=public_config)


@pytest.mark.parametrize("field", ["source", "generation_path", "cursor"])
def test_public_prepare_rejects_source_generation_cursor_splice(public_config, field):
    source = public_config.source
    bad = {"source": source.generation_path, "generation_path": source.source,
           "cursor": replace(source.cursor, offset=source.cursor.offset + 1)}[field]
    with pytest.raises(producer.ProducerRefused, match="^producer_refused$"):
        producer.prepare_public_delta(enabled=True, config=replace(public_config, source=replace(source, **{field: bad})))
