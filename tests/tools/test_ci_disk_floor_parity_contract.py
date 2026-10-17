"""Draft parity CI contract; RED/GREEN not run by Tools.
Only pure validators are compiled: no installer, host probe or main execution.
"""
import ast
from copy import deepcopy
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
PARITY = "disk-floor-windows-parity"
HEAD = "90afe064ecf7ad67c6a76150129a4fe63b3fc3c3"
EXPECTED_HEAD = "${{ github.event.pull_request.head.sha || github.sha }}"
FUNCTIONS = (
    "test_budget_parameter_is_long_and_unknown_by_default",
    "test_three_checks_precede_each_write_boundary_including_dry_run",
    "test_temp_path_and_probe_require_nonredirected_fixed_storage",
    "test_extracted_helper_budget_matrix_and_distinct_volumes",
)
CLASS = "tests.tools.test_wd_reboot_deploy_disk_floor"
FROZEN_RUNS = [
  "pip install --upgrade pip\npip install -r requirements-ci.txt\n",
  "HITS=$(grep -cn '[^_]eval(' core/symbolic_solver.py | grep -v safe_eval || true)\nRAW=$(grep -n 'eval(' core/symbolic_solver.py | grep -v safe_eval | wc -l)\nif [ \"$RAW\" -gt 0 ]; then\n  grep -n 'eval(' core/symbolic_solver.py | grep -v safe_eval\n  echo \"Raw eval() found in symbolic_solver.py!\"\n  exit 1\nfi\necho \"No raw eval() in solver\"\n",
  "python -c \"\nfrom core.safe_eval import safe_eval, SafeEvalError\nassert safe_eval('2+3', {}) == 5\ntry:\n    safe_eval('__import__(\\\"os\\\")', {})\n    exit(1)\nexcept SafeEvalError:\n    pass\nprint('Security OK')\n\"\n",
  "pytest tests/ -x -q --tb=short -p no:warnings\npython -m pytest sandbox/mind/test_mind_capture_producer_reader.py sandbox/mind/test_mind_capture_producer_state.py sandbox/mind/test_join_turn.py sandbox/mind/test_join_card.py sandbox/mind/test_join_card_delivery.py sandbox/mind/step1/test_adapter.py sandbox/mind/step1/test_lane_view.py sandbox/mind/capture/test_capacity_lens.py sandbox/mind/capture/test_overlap_lens.py sandbox/mind/test_mind_chain_driver.py -x -q --tb=short -p no:warnings\n",
  "# Fail closed: a failed collection, or a summary that is not exactly one\n# \"<N> tests collected in <T>s\" line, must not pass (K 011920df job\n# 113510581050 read \"28144 13 80\" from the duration and still passed).\nif ! OUT=$(pytest tests/ --collect-only -q 2>/dev/null); then\n  echo \"Test collection failed\"\n  exit 1\nfi\n# Exactly one summary-like line, and it is the last line; the count has\n# at most 9 digits so the integer test below cannot overflow. From 60 s on\n# pytest appends \" (H:MM:SS)\"; a longer suffix (a day count) is refused.\nSUMMARIES=$(printf '%s\\n' \"$OUT\" | grep -cE '^[0-9]+ tests? collected' || true)\nif [ \"$SUMMARIES\" != \"1\" ]; then\n  echo \"Expected exactly one collection summary, found: $SUMMARIES\"\n  exit 1\nfi\nLAST=$(printf '%s\\n' \"$OUT\" | tail -1)\nif ! [[ \"$LAST\" =~ ^([1-9][0-9]{0,8})\\ tests?\\ collected\\ in\\ [0-9]+(\\.[0-9]+)?s(\\ \\([0-9]{1,2}:[0-5][0-9]:[0-5][0-9]\\))?$ ]]; then\n  echo \"Unrecognized collection summary: $LAST\"\n  exit 1\nfi\nCOUNT=${BASH_REMATCH[1]}\necho \"Test count: $COUNT\"\nif ! [ \"$COUNT\" -ge 4200 ]; then\n  echo \"Test count dropped below 4200!\"\n  exit 1\nfi\n# Keep the existing tests/ floor independent: Mind must never mask a drop.\nif ! MIND_OUT=$(python -m pytest sandbox/mind/test_mind_capture_producer_reader.py sandbox/mind/test_mind_capture_producer_state.py sandbox/mind/test_join_turn.py sandbox/mind/test_join_card.py sandbox/mind/test_join_card_delivery.py sandbox/mind/step1/test_adapter.py sandbox/mind/step1/test_lane_view.py sandbox/mind/capture/test_capacity_lens.py sandbox/mind/capture/test_overlap_lens.py sandbox/mind/test_mind_chain_driver.py --collect-only -q 2>/dev/null); then\n  echo \"Mind test collection failed\"\n  exit 1\nfi\nMIND_SUMMARIES=$(printf '%s\\n' \"$MIND_OUT\" | grep -cE '^[0-9]+ tests? collected' || true)\nif [ \"$MIND_SUMMARIES\" != \"1\" ]; then\n  echo \"Expected exactly one Mind collection summary\"\n  exit 1\nfi\nMIND_LAST=$(printf '%s\\n' \"$MIND_OUT\" | tail -1)\nif ! [[ \"$MIND_LAST\" =~ ^([1-9][0-9]{0,8})\\ tests?\\ collected\\ in\\ [0-9]+(\\.[0-9]+)?s(\\ \\([0-9]{1,2}:[0-5][0-9]:[0-5][0-9]\\))?$ ]]; then\n  echo \"Unrecognized Mind collection summary\"\n  exit 1\nfi\necho \"Mind test count: ${BASH_REMATCH[1]}\"\n",
  "echo \"=== Checking for eval() outside safe_eval ===\"\nEVAL_HITS=$(grep -rn \"eval(\" --include=\"*.py\" core/ waggledance/ \\\n  | grep -v safe_eval | grep -v \"# noqa\" | grep -v \"test_\" \\\n  | grep -v '\\.eval()' | grep -v 'retrieval(' | grep -v 'evaluate(' | wc -l)\necho \"eval() hits outside safe_eval: $EVAL_HITS\"\nif [ \"$EVAL_HITS\" -gt 0 ]; then\n  grep -rn \"eval(\" --include=\"*.py\" core/ waggledance/ \\\n    | grep -v safe_eval | grep -v \"# noqa\" | grep -v \"test_\" \\\n    | grep -v '\\.eval()' | grep -v 'retrieval(' | grep -v 'evaluate('\n  echo \"Unauthorized eval() found!\"\n  exit 1\nfi\necho \"No unauthorized eval()\"\n\necho \"=== Checking for plaintext MQTT ===\"\ngrep -rn \"port.*1883\" --include=\"*.py\" core/ waggledance/ | grep -v test | grep -v \"# TLS\" && exit 1 || echo \"No hardcoded 1883\"\n"
]

def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

def _step(job, name):
    matches = [s for s in job["steps"] if s.get("name") == name]
    assert len(matches) == 1
    return matches[0]

def _source(workflow):
    text = _step(workflow["jobs"][PARITY], "Run verified disk-floor parity")["run"]
    return text[text.index("# parity-python-begin"):text.index("# parity-python-end")]

def _assert_workflow(workflow):
    assert set(workflow) == {"name", True, "jobs"}
    assert workflow[True] == {"push": {"branches": ["main", "master"]},
                              "pull_request": {"branches": ["main", "master"]},
                              "workflow_dispatch": None}
    jobs = workflow["jobs"]
    assert set(jobs) == {"test", "security-scan", PARITY}
    matrix = jobs["test"]
    assert "name" not in matrix
    assert matrix["strategy"] == {"fail-fast": False,
                                   "matrix": {"python-version": ["3.11", "3.12", "3.13"]}}
    assert matrix["runs-on"] == "ubuntu-latest"
    assert matrix["needs"] == [PARITY] and matrix["if"] == "${{ always() }}"
    assert "continue-on-error" not in matrix
    guard = matrix["steps"][0]
    assert guard["name"] == "Require Windows disk-floor parity"
    assert guard["if"] == "${{ always() }}" and guard["shell"] == "bash"
    assert guard["env"] == {"PARITY_RESULT": "${{ needs.disk-floor-windows-parity.result }}"}
    assert set(guard) == {"name", "if", "shell", "env", "run"}
    original = [s["run"] for s in matrix["steps"][1:] if "run" in s]
    original += [s["run"] for s in jobs["security-scan"]["steps"] if "run" in s]
    assert original == FROZEN_RUNS
    job = jobs[PARITY]
    assert job["name"] == PARITY and job["runs-on"] == "windows-latest"
    assert type(job["timeout-minutes"]) is int and job["timeout-minutes"] == 10
    assert "if" not in job and "strategy" not in job and "continue-on-error" not in job
    assert job["permissions"] == {"contents": "read"}
    assert job["env"] == {"WD_PARITY_EXPECTED_HEAD": EXPECTED_HEAD}
    assert job["steps"][0] == {"uses": "actions/checkout@v7",
                              "with": {"ref": EXPECTED_HEAD, "fetch-depth": 0, "persist-credentials": False}}
    run = _step(job, "Run verified disk-floor parity")
    assert set(run) == {"name", "shell", "run"} and run["shell"] == "powershell"
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in run["run"]
    source = _source(workflow)
    for required in ("head, run, attempt, token = parity_context(os.environ)",
                     "hosts[label] = validate_host(label, found, observed)",
                     "if tested.returncode != 0:", "pairs = validate_junit(xml, token)",
                     "if checked != head:", "exist_ok=False", "junit_suite_name={token}",
                     'if __name__ == "__main__":', "    main()"):
        assert required in source
    assert 'target = "tests/tools/test_wd_reboot_deploy_disk_floor.py"' in source
    install = _step(job, "Install existing CI dependencies")
    assert install["shell"] == "powershell"
    assert "python -m pip install -r requirements-ci.txt" in install["run"]
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in install["run"]
    assert not [s for s in job["steps"] if "continue-on-error" in s]

def _helpers():
    tree = ast.parse(_source(_workflow()))
    names = {"parity_context", "validate_host", "validate_junit"}
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                   and n.targets[0].id in {"CASE_FUNCTIONS", "CASE_CLASS"}]
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in definitions} == names and len(assignments) == 2
    scope = {"re": re, "ntpath": ntpath, "ET": ET}
    for node in assignments:
        scope[node.targets[0].id] = ast.literal_eval(node.value)
    assert tuple(scope["CASE_FUNCTIONS"]) == FUNCTIONS and scope["CASE_CLASS"] == CLASS
    # Only these three pure definitions: no main/import/probe/file write.
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "<pure-parity-validators>", "exec"), scope)
    return scope

def _context():
    return _helpers()["parity_context"](
        {"WD_PARITY_EXPECTED_HEAD": HEAD, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"})

def _xml(token):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name=token, tests="8", failures="0", errors="0", skipped="0")
    for name in FUNCTIONS:
        for host in ("ps5", "ps7"):
            ET.SubElement(suite, "testcase", classname=CLASS, name=f"{name}[{host}]", time="0.01")
    return root

def _bash():
    if sys.platform == "win32":
        done = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True, timeout=10)
        path = Path(done.stdout.strip()).parents[2] / "bin/bash.exe"
        assert path.is_file(), "Git Bash required, no WSL fallback"
        return str(path)
    path = shutil.which("bash")
    assert path
    return path

def test_workflow_keeps_old_names_full_suite_and_count_blocks():
    _assert_workflow(_workflow())

@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled", "neutral", "", "SUCCESS", " success"])
def test_required_join_rejects_every_non_success_result(result):
    # MUTANT_PARITY_FAILURE_SKIPS_REQUIRED_JOIN / MUTANT_CANCELLED_MISSING_PARITY_ACCEPTED
    workflow = _workflow()
    _assert_workflow(workflow)
    script = workflow["jobs"]["test"]["steps"][0]["run"]
    done = subprocess.run([_bash(), "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                          capture_output=True, text=True, env=dict(os.environ, PARITY_RESULT=result), timeout=10)
    assert (done.returncode == 0) is (result == "success"), done.stdout + done.stderr

@pytest.mark.parametrize("mutant", ["unwired", "conditional", "renamed", "matrix", "run-block", "no-junit"])
def test_named_workflow_mutants_are_rejected_after_positive_control(mutant):
    workflow = _workflow()
    _assert_workflow(workflow)
    bad = deepcopy(workflow)
    if mutant == "unwired":  # MUTANT_PARITY_JOB_UNWIRED
        bad["jobs"]["test"]["needs"] = []
    elif mutant == "conditional":  # MUTANT_PARITY_JOIN_SKIPPED_AFTER_FAILURE
        bad["jobs"]["test"]["if"] = "${{ success() }}"
    elif mutant == "renamed":  # MUTANT_REQUIRED_CONTEXT_RENAMED
        bad["jobs"]["test"]["name"] = "replacement"
    elif mutant == "matrix":  # MUTANT_OS_AXIS_RENAMES_CHECKS
        bad["jobs"]["test"]["strategy"]["matrix"]["os"] = ["ubuntu-latest"]
    elif mutant == "run-block":  # MUTANT_FULL_SUITE_REPLACED
        _step(bad["jobs"]["test"], "Run tests")["run"] = "pytest tests/tools/\n"
    else:  # MUTANT_JUNIT_VALIDATOR_UNWIRED
        step = _step(bad["jobs"][PARITY], "Run verified disk-floor parity")
        step["run"] = step["run"].replace("pairs = validate_junit(xml, token)", "pairs = []")
    with pytest.raises((AssertionError, KeyError)):
        _assert_workflow(bad)

@pytest.mark.parametrize("label,major,minor,edition", [("ps5", 5, 1, "Desktop"), ("ps7", 7, 6, "Core")])
def test_exact_host_identity_and_named_wrong_host_mutants(label, major, minor, edition):
    # MUTANT_WRONG_HOST_MAJOR_EDITION_PATH_OR_PLATFORM_ACCEPTED
    check = _helpers()["validate_host"]
    path = r"C:\verified\powershell.exe" if label == "ps5" else r"C:\verified\pwsh.exe"
    good = dict(platform="Win32NT", executable=path, major=major, minor=minor, edition=edition, pid=123)
    assert check(label, path, good) == good
    for change in ({"major": 99}, {"edition": "Other"}, {"executable": r"C:\proxy.exe"},
                   {"platform": "Unix"}, {"major": True}, {"pid": False}):
        with pytest.raises(ValueError):
            check(label, path, dict(good, **change))
    with pytest.raises(ValueError):  # MUTANT_PS5_OR_PS7_MISSING_ACCEPTED
        check(label, "", good)

@pytest.mark.parametrize("field", ["WD_PARITY_EXPECTED_HEAD", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"])
def test_context_refuses_missing_or_invalid_identity(field):
    # MUTANT_MISSING_HEAD_RUN_ATTEMPT_ACCEPTED
    env = dict(WD_PARITY_EXPECTED_HEAD=HEAD, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2")
    check = _helpers()["parity_context"]
    assert check(env) == _context()
    env[field] = ""
    with pytest.raises(ValueError):
        check(env)

@pytest.mark.parametrize("mutant", ["missing", "extra", "duplicate", "class", "skip", "failure", "error", "count",
                                   "head", "run", "attempt", "empty"])
def test_exact_eight_case_junit_and_named_mutants(mutant):
    # MUTANT_MISSING_EXTRA_DUPLICATE_SKIPPED_FAILED_CASE_OR_STALE_CONTEXT_ACCEPTED
    check = _helpers()["validate_junit"]
    token = _context()[3]
    root = _xml(token)
    assert len(check(ET.tostring(root), token)) == 8
    suite = root[0]
    if mutant == "missing":
        suite.remove(suite[-1])
    elif mutant == "extra":
        ET.SubElement(suite, "testcase", classname=CLASS, name="extra", time="0.01")
    elif mutant == "duplicate":
        suite[-1].set("name", suite[0].get("name"))
    elif mutant == "class":
        suite[0].set("classname", "wrong.module")
    elif mutant in ("skip", "failure", "error"):
        ET.SubElement(suite[0], {"skip": "skipped"}.get(mutant, mutant))
    elif mutant == "count":
        suite.set("tests", "7")
    elif mutant in ("head", "run", "attempt"):
        head, run, attempt, _ = _context()
        values = dict(head=head, run=run, attempt=attempt)
        values[mutant] = "f" * 40 if mutant == "head" else "999"
        suite.set("name", "wd-disk-{head}-{run}-{attempt}".format(**values))
    data = b"" if mutant == "empty" else ET.tostring(root)
    with pytest.raises((ValueError, ET.ParseError)):
        check(data, token)


@pytest.mark.parametrize("tag", [
    "rerunFailure", "rerunError", "flakyFailure", "flakyError", "unknown-result"
])
def test_junit_refuses_nonstandard_case_result_children(tag):
    check = _helpers()["validate_junit"]
    token = _context()[3]
    root = _xml(token)
    assert len(check(ET.tostring(root), token)) == 8
    ET.SubElement(root[0][0], tag)
    with pytest.raises(ValueError, match="unsupported testcase child"):
        check(ET.tostring(root), token)


@pytest.mark.parametrize("path", [
    r"\\server\share\pwsh.exe", r"\\?\C:\verified\pwsh.exe"
])
def test_host_refuses_unc_and_device_path_aliases(path):
    check = _helpers()["validate_host"]
    local = r"C:\verified\pwsh.exe"
    observed = dict(platform="Win32NT", executable=local, major=7,
                    minor=6, edition="Core", pid=123)
    assert check("ps7", local, observed) == observed
    with pytest.raises(ValueError, match="local drive-absolute"):
        check("ps7", path, dict(observed, executable=path))
