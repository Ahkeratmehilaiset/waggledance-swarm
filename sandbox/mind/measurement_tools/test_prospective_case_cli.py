"""Source-only invented bytes; no real collector/native/model access."""
import json

import pytest

from sandbox.mind.measurement_tools import prospective_case_cli as cli


def test_dormant_cli_never_reads_even_explicit_paths(monkeypatch, capsys):
    reads = []
    monkeypatch.setattr(cli, "_read", lambda *a, **k: reads.append(a), raising=False)
    assert cli.main(["--phase", "prepass_inputs"]) == 2
    assert reads == []
    assert json.loads(capsys.readouterr().out)["status"] == "STOP_NOT_READY"


def test_unknown_phase_and_unknown_option_are_sanitized(capsys):
    assert cli.main(["--execute", "--phase", "SYNTHETIC_PRIVATE_SENTINEL"]) == 2
    text = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in text


def test_cannot_release_score_or_invoke_worker_from_this_cli(capsys):
    assert cli.main(["--execute", "--phase", "score"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "STOP_NOT_READY"


def test_no_private_read_before_missing_consumed_input_seal_transition(monkeypatch, capsys):
    reads = []
    def read(*args, **kwargs):
        reads.append(args)
        raise OSError("SYNTHETIC_PRIVATE_SENTINEL")
    monkeypatch.setattr(cli, "_read", read, raising=False)
    args = ["--execute", "--phase", "prepass_inputs"]
    for field in ("protocol", "protocol-sha256", "preregistration", "preregistration-sha256", "go", "go-sha256",
                  "runner-binding", "runner-binding-sha256", "collection-result-sha256"):
        args.extend(["--" + field, "SYNTHETIC_DECLARED_VALUE"])
    assert cli.main(args) == 2
    assert reads == []
    assert json.loads(capsys.readouterr().out)["reason"] == "case_input_seal_transition_not_implemented"
