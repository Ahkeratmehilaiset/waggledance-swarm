"""Synthetic CLI/packaging checks; no bridge, real Git, or merge execution."""
from __future__ import annotations

import importlib.util
import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = "tools/classify_added_tests_readonly.py"


def test_readonly_cli_is_present():
    assert importlib.util.find_spec("tools.classify_added_tests_readonly") is not None


def test_readonly_cli_is_an_ordinary_package_entrypoint():
    definition = json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_text())
    assert ENTRYPOINT in definition["python_files"]
    assert definition["python_entrypoints"]["classify_added_tests_readonly"] == ENTRYPOINT
    assert ENTRYPOINT not in definition["manual_execution_capability"]["entrypoints"].values()


def arguments():
    return ["--repository", str(ROOT / "synthetic-repository-not-opened"),
            "--git", str(ROOT / "synthetic-tool-not-opened" / "git.exe"),
            "--base", "1" * 40, "--head", "2" * 40,
            "--base-tree", "3" * 40, "--head-tree", "4" * 40,
            "--target", "5" * 40, "--path", "tests/test_synthetic_new.py"]


@pytest.fixture
def cli(monkeypatch):
    module = importlib.import_module("tools.classify_added_tests_readonly")

    def forbidden(*args, **kwargs):
        raise AssertionError("No real subprocess or merge may execute in these tests")

    monkeypatch.setattr("subprocess.run", forbidden)
    return module


def test_exact_context_and_complete_paths_reach_only_the_classifier(cli, monkeypatch, capsys):
    calls = []
    proof = {"status": "rederived_git_added_regular_tests", "head": "2" * 40}
    result = {"ab_class": "b", "a_hits": [], "unrecognized": [],
              "reason": "synthetic exact proof", "git_evidence": proof}

    def classifier(paths, **kwargs):
        calls.append((paths, kwargs))
        return result

    monkeypatch.setattr(cli, "classify_ab", classifier)
    assert cli.main(arguments() + ["--path", "tests/test_second_synthetic.py"]) == 0
    output = json.loads(capsys.readouterr().out)
    context = {"base": "1" * 40, "head": "2" * 40, "base_tree": "3" * 40,
               "head_tree": "4" * 40, "target": "5" * 40,
               "cwd": str(ROOT / "synthetic-repository-not-opened"),
               "git": str(ROOT / "synthetic-tool-not-opened" / "git.exe")}
    assert calls == [(["tests/test_synthetic_new.py", "tests/test_second_synthetic.py"],
                      {"git_context": context})]
    assert output["classification"] == result
    assert output["input_context"] == context
    assert output["input_provenance"] == {"cwd": context["cwd"], "git": context["git"]}
    assert output["evidence_status"] == proof["status"]
    assert output["read_only"] is True
    assert output["authority_effect"] == "none"
    assert not {"admitted", "signature", "merge_command", "trusted"} & output.keys()
    assert "standing_consensus_sign" not in calls[0][1]


@pytest.mark.parametrize("classification", ["a", "b"])
def test_legacy_classification_without_proof_is_never_upgraded(cli, monkeypatch, capsys,
                                                            classification):
    result = {"ab_class": classification, "a_hits": [], "unrecognized": [],
              "reason": "synthetic legacy result"}
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: result)
    assert cli.main(arguments()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["classification"] == result
    assert output["evidence_status"] == "not_established"
    assert "git_evidence" not in output["classification"]
    assert output["authority_effect"] == "none"


@pytest.mark.parametrize("flag,value", [
    ("--repository", "relative"), ("--git", "git"),
    ("--git", str(ROOT / "not-git.exe")), ("--head", "main"),
    ("--head", "A" * 40), ("--base-tree", "short"),
    ("--repository", str(ROOT) + "\n"),
])
def test_invalid_inputs_are_refused_before_classification(cli, monkeypatch, flag, value):
    called = []
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: called.append(True))
    args = arguments()
    args[args.index(flag) + 1] = value
    with pytest.raises(SystemExit) as refusal:
        cli.main(args)
    assert refusal.value.code == 2
    assert called == []


@pytest.mark.parametrize("flag", ["--apply", "--standing-consensus-sign", "--added-only",
                                  "--request-id", "--rep"])
def test_no_admission_write_or_abbreviated_flags(cli, monkeypatch, flag):
    called = []
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: called.append(True))
    with pytest.raises(SystemExit) as refusal:
        cli.main(arguments() + [flag])
    assert refusal.value.code == 2
    assert called == []


@pytest.mark.parametrize("flag", ["--repository", "--git", "--base", "--head",
                                  "--base-tree", "--head-tree", "--target"])
def test_context_options_are_required_once_not_inferred(cli, monkeypatch, flag):
    called = []
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: called.append(True))
    args = arguments()
    index = args.index(flag)
    with pytest.raises(SystemExit) as missing:
        cli.main(args[:index] + args[index + 2:])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as duplicate:
        cli.main(args + [flag, args[index + 1]])
    assert duplicate.value.code == 2
    assert called == []


def test_complete_path_argument_is_required(cli, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: called.append(True))
    with pytest.raises(SystemExit) as missing:
        cli.main(arguments()[:-2])
    assert missing.value.code == 2
    assert called == []


@pytest.mark.parametrize("failure", [OSError, ValueError, TypeError])
def test_expected_classification_failure_reports_no_proof_or_private_details(cli, monkeypatch,
                                                                         capsys, failure):
    def classifier(*args, **kwargs):
        raise failure("synthetic private error detail must not be printed")

    monkeypatch.setattr(cli, "classify_ab", classifier)
    assert cli.main(arguments()) == 2
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["evidence_status"] == "not_established"
    assert output["error"] == "classification_unavailable"
    assert output["authority_effect"] == "none"
    assert "classification" not in output
    assert "private error detail" not in captured.out + captured.err


def test_non_object_result_cannot_look_verified(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "classify_ab", lambda *args, **kwargs: None)
    assert cli.main(arguments()) == 2
    assert json.loads(capsys.readouterr().out)["evidence_status"] == "not_established"
