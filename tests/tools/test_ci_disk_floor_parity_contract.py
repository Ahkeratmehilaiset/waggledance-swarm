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


# TESTS-FIRST HB R1-R6. Run these six against the unchanged base first.
# Execute the real inline main/fixture/_ps with stdlib doubles, never a process,
# installer or volume measurement. Missing new symbols are not RED evidence.
class _BindingPath:
    def __init__(self, owner, path):
        self.owner, self.path = owner, ntpath.normpath(str(path))
    def __str__(self):
        return self.path
    def __fspath__(self):
        return self.path
    def __truediv__(self, other):
        return _BindingPath(self.owner, ntpath.join(self.path, str(other)))
    def __eq__(self, other):
        return isinstance(other, _BindingPath) and self.path.casefold() == other.path.casefold()
    @property
    def parent(self):
        return _BindingPath(self.owner, ntpath.dirname(self.path))
    @property
    def name(self):
        return ntpath.basename(self.path)
    def relative_to(self, root):
        if ntpath.commonpath([self.path, str(root)]).casefold() != str(root).casefold():
            raise ValueError("test path escape")
        return ntpath.relpath(self.path, str(root))
    def resolve(self):
        return self
    def lstat(self):
        from types import SimpleNamespace
        key = self.path.casefold()
        if key not in self.owner.files and key not in self.owner.dirs:
            raise FileNotFoundError(self.path)
        return SimpleNamespace(st_file_attributes=0, st_size=len(self.owner.files.get(key, b"")))
    stat = lstat
    def is_symlink(self):
        return False
    def is_file(self):
        return self.path.casefold() in self.owner.files
    def read_bytes(self):
        return self.owner.files[self.path.casefold()]
    def mkdir(self, parents=False, exist_ok=False):
        key = self.path.casefold()
        if key in self.owner.dirs and not exist_ok:
            raise FileExistsError(self.path)
        self.owner.dirs.add(key)
        if parents and self.parent != self:
            self.parent.mkdir(parents=True, exist_ok=True)
    def iterdir(self):
        prefix = self.path.casefold() + "\\"
        for key in sorted(set(self.owner.files) | self.owner.dirs):
            if key.startswith(prefix) and "\\" not in key[len(prefix):]:
                yield _BindingPath(self.owner, key)
    def open(self, mode, encoding="utf-8"):
        import io
        assert mode == "x"
        key, owner = self.path.casefold(), self.owner
        if key in owner.files:
            raise FileExistsError(self.path)
        owner.files[key] = b""
        class Sink(io.StringIO):
            def __exit__(self, *args):
                owner.files[key] = self.getvalue().encode(encoding)
                return super().__exit__(*args)
        return Sink()


class _BindingDoubles:
    def __init__(self, *, bad_pid=False, dirty_target=False, drift_deploy=False, stale_case=False, eol_crlf=False):
        import hashlib
        import json
        import base64
        import io
        from types import SimpleNamespace
        self.hashlib, self.json, self.base64 = hashlib, json, base64
        self.bad_pid, self.drift_deploy, self.stale_case = bad_pid, drift_deploy, stale_case
        self.repo = _BindingPath(self, r"C:\repo")
        self.token = f"wd-disk-{HEAD}-123-2"
        self.output = self.repo / ".codex-audit" / "windows-disk-parity" / self.token
        self.target = "tests/tools/test_wd_reboot_deploy_disk_floor.py"
        self.deploy = "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
        self.blobs = {self.target: b"fixture\n", self.deploy: b"deploy\n"}
        self.env = {"WD_PARITY_EXPECTED_HEAD": HEAD, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
        self.paths = {"powershell": r"C:\verified\powershell.exe", "pwsh": r"C:\verified\pwsh.exe",
                      "git": r"C:\runner\git.exe"}
        self.files = {(self.repo / name).path.casefold(): data for name, data in self.blobs.items()}
        self.files.update({path.casefold(): label.encode() for label, path in self.paths.items()})
        self.dirty_target = dirty_target
        if eol_crlf:
            for name, blob in self.blobs.items():
                self.files[(self.repo / name).path.casefold()] = blob.replace(b"\n", b"\r\n")
        if dirty_target:
            self.files[(self.repo / self.target).path.casefold()] = b"not the expected fixture\n"
        self.dirs = {r"c:\repo", "c:\\"}
        self.launches, self.pytest_calls, self.pytest_env = [], 0, None
        owner = self
        class Child:
            def __init__(self, argv, **kwargs):
                self.argv, self.pid, self.returncode = list(argv), 500 + len(owner.launches), 0
                owner.launches.append(self.argv)
            def communicate(self, input=None, timeout=None):
                actual = self.argv[0]
                ps7 = actual.lower().endswith("pwsh.exe")
                identity = dict(platform="Win32NT", executable=actual, major=7 if ps7 else 5,
                                minor=6 if ps7 else 1, edition="Core" if ps7 else "Desktop",
                                pid=self.pid + int(owner.bad_pid))
                script = base64.b64decode(self.argv[-1]).decode("utf-16-le")
                if "# strict-parity-envelope" in script:
                    identity["source_text_sha256"] = hashlib.sha256(b"deploy\n").hexdigest()
                    return json.dumps(dict(identity=identity, data={"ok": True})), ""
                return json.dumps(identity), ""
            def kill(self):
                self.returncode = -9
        def run(argv, **kwargs):
            if "pytest" not in argv:
                if "input" in kwargs:
                    owner.launches.append(list(argv))
                    return SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
                child = Child(argv)
                stdout, stderr = child.communicate()
                return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)
            owner.pytest_calls += 1
            owner.pytest_env = kwargs.get("env")
            binding = json.loads((owner.pytest_env or {}).get("WD_PARITY_BINDING_JSON", "{}")) or owner.binding()
            raw = (owner.pytest_env or {}).get("WD_PARITY_BINDING_JSON") or json.dumps(binding, sort_keys=True, separators=(",", ":"))
            binding_sha = hashlib.sha256(raw.encode()).hexdigest()
            folder = owner.output / "cases"
            folder.mkdir(parents=True, exist_ok=True)
            for function in FUNCTIONS:
                for label in ("ps5", "ps7"):
                    host = binding["hosts"][label]
                    record = dict(schema="wd.windows-disk-case.v1", token=owner.token, binding_sha256=binding_sha,
                                  node=f"{function}[{label}]", label=label, path=host["path"],
                                  before_sha256=host["sha256"], after_sha256=host["sha256"],
                                  launched_pid=700, observed_pid=700, platform="Win32NT", major=host["major"],
                                  minor=host["minor"], edition=host["edition"],
                                  source_text_sha256=hashlib.sha256(b"deploy\n").hexdigest(), exit=0)
                    if owner.stale_case and function == FUNCTIONS[-1] and label == "ps7":
                        record["token"] = owner.token.rsplit("-", 1)[0] + "-999"
                    owner.files[(folder / f"{function}__{label}.json").path.casefold()] = json.dumps(record).encode()
            report = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--junitxml="))
            owner.files[ntpath.normpath(report).casefold()] = ET.tostring(_xml(owner.token))
            if owner.dirty_target:
                owner.files[(owner.repo / owner.target).path.casefold()] = owner.blobs[owner.target]
            if owner.drift_deploy:
                owner.files[(owner.repo / owner.deploy).path.casefold()] = b"changed Deploy\n"
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        def check_output(argv, **kwargs):
            if "show" in argv:
                return owner.blobs[argv[-1].split(":", 1)[1]]
            return HEAD if kwargs.get("text") else HEAD.encode()
        def path(value):
            return value if isinstance(value, _BindingPath) else _BindingPath(owner, value)
        path.cwd = lambda: owner.repo
        self.scope = dict(base64=base64, hashlib=hashlib, json=json, ntpath=ntpath, re=re, ET=ET, Path=path,
                          os=SimpleNamespace(name="nt", environ=self.env, path=ntpath),
                          sys=SimpleNamespace(executable=r"C:\runner\python.exe", stderr=io.StringIO()),
                          shutil=SimpleNamespace(which=lambda name: self.paths.get(name)),
                          subprocess=SimpleNamespace(Popen=Child, run=run, check_output=check_output,
                                                     PIPE=-1, TimeoutExpired=subprocess.TimeoutExpired))
    def binding(self):
        identities = {}
        for name, blob in self.blobs.items():
            raw = self.files[(self.repo / name).path.casefold()]
            identities[name] = dict(blob=self.hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest(),
                                    raw_sha256=self.hashlib.sha256(raw).hexdigest(),
                                    text_sha256=self.hashlib.sha256(blob).hexdigest(), eol="exact" if raw == blob else "lf_to_crlf")
        hosts = {}
        for label, executable in (("ps5", "powershell"), ("ps7", "pwsh")):
            hosts[label] = dict(path=self.paths[executable],
                               sha256=self.hashlib.sha256(self.files[self.paths[executable].casefold()]).hexdigest(),
                               major=5 if label == "ps5" else 7, minor=1 if label == "ps5" else 6,
                               edition="Desktop" if label == "ps5" else "Core")
        return dict(schema="wd.windows-disk-binding.v1", head=HEAD, run_id="123", run_attempt="2",
                    token=self.token, output=str(self.output), hosts=hosts, inputs=identities)
    def main(self):
        tree = ast.parse(_source(_workflow()))
        allowed = {"CASE_FUNCTIONS", "CASE_CLASS"}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) or
                 isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                 and n.targets[0].id in allowed]
        scope = dict(self.scope)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-parity-main-with-doubles>", "exec"), scope)
        scope["main"]()
    def fixture(self, *, strict, replacement=None, param="powershell"):
        from types import SimpleNamespace
        # Read Source as data only. No imports/top-level target code is executed.
        source = (ROOT / self.target).read_text(encoding="utf-8")
        tree = ast.parse(source)
        nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if isinstance(node, ast.FunctionDef):
                    node.decorator_list = []
                nodes.append(node)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "_PARITY_FUNCTIONS":
                nodes.append(node)
        env = dict(self.env) if strict else {}
        if strict:
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / "cases").mkdir(exist_ok=True)
            env.update(WD_PARITY_STRICT="1", WD_PARITY_BINDING_JSON=self.json.dumps(self.binding(), sort_keys=True, separators=(",", ":")))
        paths = dict(self.paths)
        if replacement is not None:
            paths[param] = replacement
        scope = dict(self.scope, ROOT=self.repo, DEPLOY_SOURCE="deploy\n", pytest=pytest,
                     os=SimpleNamespace(name="nt", environ=env, path=ntpath),
                     shutil=SimpleNamespace(which=lambda name: paths.get(name)))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<actual-host-fixture-with-doubles>", "exec"), scope)
        label = {"powershell": "ps5", "pwsh": "ps7"}[param]
        request = SimpleNamespace(param=param, node=SimpleNamespace(name=f"{FUNCTIONS[0]}[{label}]"))
        host = scope["host"](request)
        assert scope["_ps"](host, "'fixed test result'") == {"ok": True}
        return host


def test_binding_r1_parent_transports_strict_context():
    # MUTANT_HOST_BINDING_NOT_TRANSPORTED: baseline runs and succeeds; assertion is RED.
    run = _BindingDoubles()
    run.main()
    assert run.pytest_calls == 1
    env = run.pytest_env or {}
    assert env.get("WD_PARITY_STRICT") == "1" and env.get("WD_PARITY_BINDING_JSON"), "R1 missing transport"
    assert run.json.loads(env["WD_PARITY_BINDING_JSON"]) == run.binding()


def test_binding_r2_actual_fixture_uses_pinned_host_not_path():
    # MUTANT_FIXTURE_RERESOLVES_DIFFERENT_HOST: positive local and strict controls first.
    ordinary = _BindingDoubles()
    assert str(ordinary.fixture(strict=False)) == ordinary.paths["powershell"]
    missing = _BindingDoubles()
    missing.paths.pop("powershell")
    with pytest.raises(pytest.skip.Exception):
        missing.fixture(strict=False)
    positive = _BindingDoubles()
    assert str(positive.fixture(strict=True)) == positive.paths["powershell"]
    ps7 = _BindingDoubles()
    assert str(ps7.fixture(strict=True, param="pwsh")) == ps7.paths["pwsh"]
    negative = _BindingDoubles()
    negative.fixture(strict=True, replacement=r"C:\other\powershell.exe")
    assert negative.launches[-1][0] == negative.paths["powershell"], "R2 fixture launched PATH replacement"


def test_binding_r3_reported_pid_must_equal_this_probe_child():
    # MUTANT_SELF_REPORTED_PID_ACCEPTED: no new symbol required on baseline.
    _BindingDoubles().main()
    with pytest.raises((ValueError, RuntimeError, AssertionError), match="PID|identity"):
        _BindingDoubles(bad_pid=True).main()


def test_binding_r4_rejects_dirty_target_before_launch_not_post_only():
    # MUTANT_TARGET_POST_ONLY_HASH: fake pytest restores dirty bytes; must never launch.
    _BindingDoubles().main()
    _BindingDoubles(eol_crlf=True).main()  # Explicit supported EOL control, not whitespace stripping.
    run = _BindingDoubles(dirty_target=True)
    with pytest.raises((ValueError, RuntimeError, AssertionError), match="Source"):
        run.main()
    assert run.pytest_calls == 0


def test_binding_r5_rejects_consumed_deploy_post_drift():
    # MUTANT_CONSUMED_DEPLOY_DRIFT_UNCHECKED: test file/JUnit remain correct.
    _BindingDoubles().main()
    with pytest.raises((ValueError, RuntimeError, AssertionError), match="Source"):
        _BindingDoubles(drift_deploy=True).main()


def test_binding_r6_rejects_stale_case_even_with_current_eight_junit():
    # MUTANT_STALE_CASE_RECEIPT_ACCEPTED: same eight case names, wrong run attempt.
    _BindingDoubles().main()
    with pytest.raises((ValueError, RuntimeError, AssertionError), match="case receipt"):
        _BindingDoubles(stale_case=True).main()
