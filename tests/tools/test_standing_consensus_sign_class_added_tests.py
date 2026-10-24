"""Synthetic measured-addition conformance; never execute installed gate surfaces."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[2]
BASE, HEAD, BASE_TREE, HEAD_TREE, TARGET = (c * 40 for c in "12345")
# An absolute Git path that is never launched: every call goes to the synthetic fake below.
GIT_FOLDER = "C:/b9-synthetic-git-not-opened/cmd" if os.name == "nt" else "/b9-synthetic-git-not-opened/cmd"
GIT = GIT_FOLDER + ("/git.exe" if os.name == "nt" else "/git")
CONTEXT = dict(base=BASE, head=HEAD, base_tree=BASE_TREE, head_tree=HEAD_TREE, target=TARGET, git=GIT,
               cwd="C:/b9-synthetic-repository-not-opened")
PATH = "tests/tools/test_b9_sample.py"
TEXT = b"def test_sample():\n    assert 1 == 1\n"
PREFIX = [GIT, "--no-pager", "--no-replace-objects", "--literal-pathspecs"]
# The parent environment of every measured call (synthetic values only): Git directory, common-dir, object,
# replace, graft, namespace and config controls plus HOME/XDG locations. None of them may reach the Git child.
HOSTILE = {"GIT_DIR": "C:/synthetic-other/.git", "GIT_COMMON_DIR": "C:/synthetic-other/common",
           "GIT_WORK_TREE": "C:/synthetic-other", "GIT_INDEX_FILE": "C:/synthetic-other/index",
           "GIT_OBJECT_DIRECTORY": "C:/synthetic-other/objects",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES": "C:/synthetic-other/alternates",
           "GIT_REPLACE_REF_BASE": "refs/synthetic-replace/", "GIT_NO_REPLACE_OBJECTS": "0",
           "GIT_GRAFT_FILE": "C:/synthetic-other/grafts", "GIT_SHALLOW_FILE": "C:/synthetic-other/shallow",
           "GIT_NAMESPACE": "synthetic", "GIT_CONFIG": "C:/synthetic-other/config",
           "GIT_CONFIG_GLOBAL": "C:/synthetic-home/.gitconfig", "GIT_CONFIG_SYSTEM": "C:/synthetic-system/gitconfig",
           "GIT_CONFIG_NOSYSTEM": "0", "GIT_CONFIG_PARAMETERS": "'core.synthetic=1'", "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "diff.external", "GIT_CONFIG_VALUE_0": "synthetic-diff",
           "GIT_EXTERNAL_DIFF": "synthetic-diff", "GIT_EXEC_PATH": "C:/synthetic-exec",
           "GIT_CEILING_DIRECTORIES": "C:/", "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
           "HOME": "C:/synthetic-home", "XDG_CONFIG_HOME": "C:/synthetic-xdg"}
PARENT_ENV = dict(HOSTILE, PATH="C:/synthetic-parent/bin", SYSTEMROOT="C:/synthetic-os")


def child_env():
    """The exact environment the measured Git child must get, built from fixed values only."""
    env = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    if os.name == "nt":
        root = PARENT_ENV["SYSTEMROOT"]
        env.update({"SYSTEMROOT": root, "WINDIR": root, "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                    "PATH": os.pathsep.join((GIT_FOLDER, os.path.join(root, "System32"), root))})
    else:
        env["PATH"] = os.pathsep.join((GIT_FOLDER, "/usr/bin", "/bin"))
    return env


def load_subject():
    charter = types.ModuleType("waggledance.core.idle_consensus_charter")
    charter.load_charter = lambda: {"synthetic": True}
    charter.evaluate_paths = lambda c, p: types.SimpleNamespace(allowed=True, reason="synthetic allow")
    enrollment = types.ModuleType("tools.bridge_rco_enrollment")
    enrollment.load_rco_enrollment_context = lambda: {"status": "unknown", "accepted": False, "root": None}
    enrollment.rco_event_enrollment_matches = lambda *a, **k: False
    modules = {"waggledance": types.ModuleType("waggledance"),
               "waggledance.core": types.ModuleType("waggledance.core"),
               "waggledance.core.idle_consensus_charter": charter,
               "tools": types.ModuleType("tools"), "tools.bridge_rco_enrollment": enrollment}
    spec = importlib.util.spec_from_file_location("_b9_synthetic_subject", SOURCE_ROOT / "tools/check_standing_consensus_sign_class.py")
    subject = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, modules):
        spec.loader.exec_module(subject)
    return subject


def blob_oid(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False).hexdigest()


def patch_for(path, data):
    oid = blob_oid(data)
    out = (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
           f"index {'0'*40}..{oid}\n").encode()
    if not data:
        return out
    lines = data.split(b"\n")
    terminated = data.endswith(b"\n")
    if terminated:
        lines.pop()
    count = len(lines)
    extent = "1" if count == 1 else f"1,{count}"
    out += f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +{extent} @@\n".encode()
    out += b"".join(b"+" + line + b"\n" for line in lines)
    if not terminated:
        out += b"\\ No newline at end of file\n"
    return out


class SyntheticGit:
    """Exact command/kwargs allowlist. An unexpected command never reaches Git."""
    def __init__(self, files=None):
        self.files = dict(files or {PATH: TEXT})
        self.commands = {}
        self.calls = []
        def put(args, data):
            self.commands[tuple(PREFIX + args)] = data
        for sha, tree in ((BASE, BASE_TREE), (HEAD, HEAD_TREE)):
            put(["rev-parse", "--verify", sha + "^{commit}"], (sha + "\n").encode())
            put(["rev-parse", "--verify", sha + "^{tree}"], (tree + "\n").encode())
        self.target_key = tuple(PREFIX + ["rev-parse", "--verify", TARGET + "^{commit}"])
        self.commands[self.target_key] = (TARGET + "\n").encode()
        self.merge_base_key = tuple(PREFIX + ["merge-base", "--all", TARGET, HEAD])
        self.commands[self.merge_base_key] = (BASE + "\n").encode()
        self.envs = []
        self.base_paths_key = tuple(PREFIX + ["ls-tree", "-r", "--name-only", "-z", BASE, "--"])
        self.commands[self.base_paths_key] = b"README.md\0tests/tools/test_existing.py\0"
        raw, names, patch = b"", b"", b""
        for path, data in self.files.items():
            p = path.encode()
            oid = blob_oid(data)
            raw += f":000000 100644 {'0'*40} {oid} A".encode() + b"\0" + p + b"\0"
            names += b"A\0" + p + b"\0"
            patch += patch_for(path, data)
            put(["ls-tree", "-z", BASE, "--", path], b"")
            put(["ls-tree", "-z", HEAD, "--", path], b"100644 blob " + oid.encode() + b"\t" + p + b"\0")
            put(["cat-file", "blob", oid], data)
        self.raw_key = tuple(PREFIX + ["diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--find-copies", "--find-copies-harder", "--raw", "--abbrev=40", "-z", BASE, HEAD, "--"])
        self.names_key = tuple(PREFIX + ["diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--find-copies", "--find-copies-harder", "--name-status", "-z", BASE, HEAD, "--"])
        self.patch_key = tuple(PREFIX + ["diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--full-index", "--src-prefix=a/", "--dst-prefix=b/", "--no-relative", BASE, HEAD, "--"])
        self.commands.update({self.raw_key: raw, self.names_key: names, self.patch_key: patch})

    def run(self, cmd, **kwargs):
        self.calls.append(tuple(cmd))
        self.envs.append(kwargs.get("env"))
        expected = dict(cwd=CONTEXT["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, check=True, timeout=10, env=child_env())
        if kwargs != expected or tuple(cmd) not in self.commands:
            raise AssertionError(f"Unexpected real-process attempt: {cmd!r}, {kwargs!r}")
        value = self.commands[tuple(cmd)]
        if isinstance(value, BaseException):
            raise value
        return subprocess.CompletedProcess(cmd, 0, stdout=value, stderr=b"")


class AddedTestsConformance(unittest.TestCase):
    def setUp(self):
        self.subject = load_subject()

    def measured(self, paths=None, fixture=None, context=None):
        fixture = fixture or SyntheticGit()
        with mock.patch("subprocess.run", side_effect=fixture.run), mock.patch.dict(os.environ, PARENT_ENV, clear=True):
            return self.subject.classify_ab([PATH] if paths is None else paths,
                                            git_context=CONTEXT if context is None else context)

    def test_every_git_read_disables_replacements_and_gets_only_a_built_environment(self):
        """RCO2 13D46D21: replace refs and inherited GIT_DIR/common-dir/object/config controls must not reach Git."""
        fixture = SyntheticGit()
        self.assertEqual("b", self.measured(fixture=fixture)["ab_class"])
        self.assertTrue(fixture.calls)
        for cmd, env in zip(fixture.calls, fixture.envs):
            self.assertEqual(PREFIX, list(cmd[:len(PREFIX)]))
            self.assertEqual(child_env(), env)
            for name, value in HOSTILE.items():
                self.assertFalse(name in env and env[name] == value, name)
            self.assertFalse({"HOME", "XDG_CONFIG_HOME", "GIT_DIR", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_PARAMETERS",
                              "GIT_CONFIG_COUNT", "GIT_CONFIG", "GIT_CONFIG_SYSTEM"} & set(env))
            self.assertNotIn("C:/synthetic-parent/bin", env["PATH"])

    def test_base_must_be_the_one_merge_base_of_target_and_head(self):
        """RCO1 F1: a caller passing head^ of a longer branch, or any other ancestor, never proves the PR change."""
        for output in (b"6" * 40 + b"\n", (BASE + "\n" + "6" * 40 + "\n").encode(), b"", (BASE + "\n").encode() * 2,
                       BASE.encode()):
            fixture = SyntheticGit(); fixture.commands[fixture.merge_base_key] = output
            with self.subTest(output=output):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(fixture=fixture))
        fixture = SyntheticGit(); fixture.commands[fixture.target_key] = b"f" * 40 + b"\n"
        self.assertEqual(self.subject.classify_ab([PATH]), self.measured(fixture=fixture))
        fixture = SyntheticGit()
        self.assertEqual(TARGET, self.measured(fixture=fixture)["git_evidence"]["target"])
        self.assertIn(fixture.merge_base_key, fixture.calls)

    def test_git_executable_must_be_an_explicit_absolute_git_path(self):
        """RCO1 F2: never a bare-name launch resolved through the current directory or PATH."""
        folder = GIT_FOLDER + "/"
        for git in ("git", "git.exe", "cmd/git.exe", folder + "python.exe", folder + "git.exe.bat", folder,
                    folder + "git\n", "", None, 7):
            with self.subTest(git=git):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(context=dict(CONTEXT, git=git)))

    def test_legacy_no_context_never_spawns_or_downgrades(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("No subprocess on legacy path")) as run:
            self.assertEqual("a", self.subject.classify_ab([PATH])["ab_class"])
            self.assertEqual("b", self.subject.classify_ab(["docs/runs/synthetic.md"])["ab_class"])
            self.assertEqual("a", self.subject.classify_ab([])["ab_class"])
            run.assert_not_called()

    def test_verified_new_regular_test_is_b(self):
        self.assertEqual("b", self.measured()["ab_class"])

    def test_receipt_binds_exact_objects_paths_and_patch(self):
        fixture = SyntheticGit()
        proof = self.measured(fixture=fixture)["git_evidence"]
        for key in ("base", "head", "base_tree", "head_tree"):
            self.assertEqual(CONTEXT[key], proof[key])
        self.assertEqual([PATH], proof["paths"])
        self.assertEqual({PATH: blob_oid(TEXT)}, proof["blobs"])
        self.assertEqual(hashlib.sha256(patch_for(PATH, TEXT)).hexdigest(), proof["diff_sha256"])
        self.assertIn(fixture.base_paths_key, fixture.calls)
        self.assertEqual(hashlib.sha256(fixture.commands[fixture.base_paths_key]).hexdigest(), proof["base_paths_sha256"])

    def test_base_casefold_file_or_directory_aliases_refuse_new_exception(self):
        aliases = ("tests/tools/test_B9_sample.py", "Tests/tools/test_other.py",
                   "tests/Tools/test_other.py", "tests/TOOLS/test_other.py",
                   "tests/tool\u017f/test_other.py", "tests./tools/test_other.py",
                   "tests/tools./test_other.py", "tests/tools/test_b9_sample.py.",
                   "tests/tools/test_b9_sample.py ")
        for existing in aliases:
            fixture = SyntheticGit()
            fixture.commands[fixture.base_paths_key] = existing.encode() + b"\0"
            with self.subTest(existing=existing):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(fixture=fixture))

    def test_base_inventory_must_be_complete_unambiguous_and_non_conflicting(self):
        for inventory in (b"tests/tools/test_B9_sample.py", b"README.md\0README.md\0",
                          b"tests/tools\0", PATH.encode() + b"\0", b"\xff\0",
                          b"tests/../tools/test_other.py\0", b"tests\\tools\\old.py\0",
                          b"/tests/tools/old.py\0", b"tests//tools/old.py\0"):
            fixture = SyntheticGit(); fixture.commands[fixture.base_paths_key] = inventory
            with self.subTest(inventory=inventory):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(fixture=fixture))

    def test_empty_base_tree_or_noncolliding_base_paths_are_supported(self):
        for inventory in (b"", b"README.md\0", b"tests/tools/test_existing.py\0"):
            fixture = SyntheticGit(); fixture.commands[fixture.base_paths_key] = inventory
            with self.subTest(inventory=inventory):
                self.assertEqual("b", self.measured(fixture=fixture)["ab_class"])

    def test_two_verified_new_tests_are_b(self):
        files = {PATH: TEXT, "tests/test_another.py": b"assert True\n"}
        self.assertEqual("b", self.measured(list(files), SyntheticGit(files))["ab_class"])

    def test_empty_crlf_no_newline_and_utf8_text(self):
        for data in (b"", b"assert True", b"assert True\r\n", b"# \xc3\xa4\nassert True\n"):
            with self.subTest(data=data):
                self.assertEqual("b", self.measured(fixture=SyntheticGit({PATH: data}))["ab_class"])

    def test_non_additions_keep_exact_legacy_result(self):
        for status in (b"M", b"D", b"R100", b"C100", b"T", b"U", b"X", b"A100", b""):
            fixture = SyntheticGit()
            fixture.commands[fixture.raw_key] = fixture.commands[fixture.raw_key].replace(b" A\0", b" " + status + b"\0")
            with self.subTest(status=status):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(fixture=fixture))

    def test_old_b_categories_stay_b_when_not_all_added_tests(self):
        paths = ["docs/runs/synthetic.md"]
        self.assertEqual(self.subject.classify_ab(paths), self.measured(paths, SyntheticGit({paths[0]: TEXT})))

    def test_mixed_old_b_source_and_new_test_stays_a(self):
        files = {PATH: TEXT, "tools/run_toy_proof.py": b"pass\n"}
        self.assertEqual(self.subject.classify_ab(list(files)), self.measured(list(files), SyntheticGit(files)))

    def test_forbidden_and_noncanonical_paths(self):
        paths = ("tests/conftest.py", "tests/__init__.py", "tests/a.pth", "tests/sitecustomize.py",
                 "tests/usercustomize.py", "tests/windows/test_sample.py", "tests/fixtures/test_sample.py",
                 "tests/corpus/test_sample.py", "tests/hooks/test_sample.py", "tests/config/test_sample.py",
                 "tests/test_config.py", "tests/test_import_hook.py", "tests/test_conformance.py",
                 "tests/tools/test_check_rco_pass_present_new.py", "tests/tools/test_secret_new.py",
                 "tests/tools/test_standing_consensus_sign_class_added_tests.py", "tests/a/../test_sample.py",
                 "tests/./test_sample.py", "./tests/test_sample.py", "/tests/test_sample.py",
                 "C:/tests/test_sample.py", "tests\\test_sample.py", "Tests/test_sample.py",
                 "tests/test_Sample.py", "tests//test_sample.py", "tests/test_\u00e4.py", "tests/test_sample.py ",
                 "tests/test_sample.py\n", "test_sample.py", "tests/test_sample.py/evil", "tests/.hidden/test_sample.py")
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.subject.classify_ab([path]), self.measured([path], SyntheticGit({path: TEXT})))

    def test_windows_device_alias_directories_are_not_new_b(self):
        for component in ("con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"):
            path = f"tests/{component}/test_sample.py"
            with self.subTest(path=path):
                self.assertEqual(self.subject.classify_ab([path]), self.measured([path], SyntheticGit({path: TEXT})))

    def test_missing_or_oversize_reader_output_is_not_evidence(self):
        for output in (None, "not bytes", b"x" * (4 * 1024 * 1024 + 1)):
            fixture = SyntheticGit(); fixture.commands[fixture.raw_key] = output
            with self.subTest(kind=type(output).__name__):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_file_count_bound_preserves_legacy(self):
        paths = [f"tests/test_case_{n}.py" for n in range(33)]
        self.assertEqual(self.subject.classify_ab(paths), self.measured(paths, SyntheticGit(dict.fromkeys(paths, TEXT))))

    def test_duplicate_casefold_collision_or_wrong_path_set(self):
        for paths in ([PATH, PATH], [PATH, PATH.upper()], ["tests/test_other.py"], [], [None]):
            with self.subTest(paths=paths):
                self.assertEqual(self.subject.classify_ab(paths), self.measured(paths))

    def test_non_string_unhashable_paths_keep_old_result(self):
        for paths in ([{}], [[]], [PATH, {}]):
            with self.subTest(paths=paths):
                self.assertEqual(self.subject.classify_ab(paths), self.measured(paths))

    def test_copy_detection_is_explicit_for_raw_and_names(self):
        fixture = SyntheticGit()
        self.assertEqual("b", self.measured(fixture=fixture)["ab_class"])
        for command in (fixture.raw_key, fixture.names_key):
            self.assertIn(command, fixture.calls)
            self.assertIn("--find-copies", command)
            self.assertIn("--find-copies-harder", command)

    def test_missing_extra_or_malformed_context(self):
        without_target = {k: v for k, v in CONTEXT.items() if k != "target"}
        without_git = {k: v for k, v in CONTEXT.items() if k != "git"}
        contexts = ({}, True, {"added_only": True}, dict(CONTEXT, added_only=True),
                    dict(CONTEXT, head="main"), dict(CONTEXT, base=BASE.upper() + "x"),
                    dict(CONTEXT, head_tree=None), dict(CONTEXT, cwd=""), dict(CONTEXT, base_tree=True),
                    without_target, without_git, dict(CONTEXT, target="main"), dict(CONTEXT, target=None))
        for context in contexts:
            with self.subTest(context=context):
                self.assertEqual(self.subject.classify_ab([PATH]), self.measured(context=context))

    def test_commit_or_tree_mismatch_fails_closed(self):
        for sha, suffix in ((BASE, "^{commit}"), (HEAD, "^{commit}"), (BASE, "^{tree}"), (HEAD, "^{tree}")):
            fixture = SyntheticGit()
            fixture.commands[tuple(PREFIX + ["rev-parse", "--verify", sha + suffix])] = b"f" * 40 + b"\n"
            with self.subTest(sha=sha, suffix=suffix):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_bad_modes_oids_or_raw_shape_fail_closed(self):
        original = SyntheticGit().commands[SyntheticGit().raw_key]
        variants = (original.replace(b"100644", b"100755"), original.replace(b"100644", b"120000"),
                    original.replace(b"100644", b"160000"), original.replace(b":000000", b":100644"),
                    original.replace(b"0" * 40, b"e" * 40), original[:-1], original + original,
                    original + b"truncated", b"", original.replace(b"\0", b" ", 1))
        for raw in variants:
            fixture = SyntheticGit(); fixture.commands[fixture.raw_key] = raw
            with self.subTest(raw=raw[:70]):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_name_status_must_agree_completely(self):
        for names in (b"", b"M\0" + PATH.encode() + b"\0", b"A\0tests/test_other.py\0",
                      b"A\0" + PATH.encode(), b"A\0" + PATH.encode() + b"\0extra\0"):
            fixture = SyntheticGit(); fixture.commands[fixture.names_key] = names
            with self.subTest(names=names):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_base_must_be_absent_and_head_regular_blob_exact(self):
        for commit, data in ((BASE, b"100644 blob " + blob_oid(TEXT).encode() + b"\t" + PATH.encode() + b"\0"),
                             (HEAD, b""), (HEAD, b"120000 blob " + blob_oid(TEXT).encode() + b"\t" + PATH.encode() + b"\0"),
                             (HEAD, b"100644 blob " + b"e"*40 + b"\t" + PATH.encode() + b"\0")):
            fixture = SyntheticGit(); fixture.commands[tuple(PREFIX + ["ls-tree", "-z", commit, "--", PATH])] = data
            with self.subTest(commit=commit, data=data):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_diff_headers_body_and_blob_are_verified_not_labels(self):
        original = patch_for(PATH, TEXT)
        variants = (b"", original[:-4], original.replace(b"new file mode 100644", b"new file mode 100755"),
                    original.replace(b"diff --git a/", b'diff --git "a/'),
                    original.replace(b"--- /dev/null", b"--- a/old.py"),
                    original.replace(b"@@ -0,0 +1,2 @@", b"@@ -0,0 +1,3 @@"),
                    original.replace(b"+    assert 1 == 1", b"+    assert False"),
                    original + b"Binary files differ\n", original + original)
        for patch in variants:
            fixture = SyntheticGit(); fixture.commands[fixture.patch_key] = patch
            with self.subTest(patch=patch[:80]):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])
        fixture = SyntheticGit(); fixture.commands[tuple(PREFIX + ["cat-file", "blob", blob_oid(TEXT)])] = b"forged\n"
        self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_binary_blob_and_oversize_evidence_fail_closed(self):
        fixture = SyntheticGit({PATH: b"nul\0byte\n"})
        self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])
        fixture = SyntheticGit(); fixture.commands[fixture.raw_key] = b"x" * (4 * 1024 * 1024 + 1)
        self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_git_failures_cannot_fall_back_to_added_labels(self):
        for error in (OSError("synthetic unavailable"), subprocess.CalledProcessError(1, ["git"]),
                      subprocess.TimeoutExpired(["git"], 10)):
            fixture = SyntheticGit(); fixture.commands[fixture.raw_key] = error
            with self.subTest(error=type(error).__name__):
                self.assertEqual("a", self.measured(fixture=fixture)["ab_class"])

    def test_existing_charter_denylist_and_unknown_charter_win(self):
        with mock.patch.object(self.subject, "evaluate_paths", return_value=types.SimpleNamespace(allowed=False, reason="denylist hit")):
            self.assertEqual("a", self.measured()["ab_class"])
        with mock.patch.object(self.subject, "load_charter", side_effect=RuntimeError("synthetic charter unavailable")):
            self.assertEqual("a", self.measured()["ab_class"])

    def test_no_standing_signature_self_activation(self):
        result = self.subject.evaluate_standing_consensus_sign(enabled=False, changed_paths=[PATH],
            bridge_consensus={}, ci_all_green=True, diff_gate_allowed=True, head_matches=True, receipt_present=True)
        self.assertFalse(result["admitted"])


if __name__ == "__main__":
    unittest.main()
