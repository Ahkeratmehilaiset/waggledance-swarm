"""The public-status CLI is default-off and never retries an uncertain write."""
import importlib
import json

import pytest


def cli():
    return importlib.import_module("tools.wd_mind_public_event_writer_cli")


def test_cli_defaults_off_without_invoking_writer(monkeypatch, capsys):
    mod = cli()
    monkeypatch.setattr(mod.writer, "publish", lambda **_: pytest.fail("unexpected write"))
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OFF"}


def test_cli_uncertain_write_is_unknown_and_not_retried(monkeypatch, capsys):
    mod = cli()
    calls = []
    def uncertain(**kwargs):
        calls.append(kwargs)
        raise mod.writer.PublicStatusWriteUncertain("sensitive diagnostic")
    monkeypatch.setattr(mod.writer, "publish", uncertain)
    assert mod.main(["--enable"]) == 3
    assert json.loads(capsys.readouterr().out) == {"status": "UNKNOWN"}
    assert len(calls) == 1


@pytest.mark.parametrize("value,expected", [
    ({"delivery_status": "canonical", "canonical_durable": True}, {"status": "WRITTEN", "delivery": "canonical"}),
    ({"delivery_status": "queued", "canonical_durable": False, "retained_wal_sha256": "a" * 64},
     {"status": "QUEUED", "retained_wal_sha256": "a" * 64}),
])
def test_cli_preserves_queued_vs_canonical(monkeypatch, capsys, value, expected):
    mod = cli()
    monkeypatch.setattr(mod.writer, "publish", lambda **_: value)
    assert mod.main(["--enable"]) == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_cli_bad_receipt_is_unknown_without_echo(monkeypatch, capsys):
    mod = cli()
    monkeypatch.setattr(mod.writer, "publish", lambda **_: {"delivery_status": "canonical", "secret": "do-not-print"})
    assert mod.main(["--enable"]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "UNKNOWN"} and "do-not-print" not in captured.out + captured.err
