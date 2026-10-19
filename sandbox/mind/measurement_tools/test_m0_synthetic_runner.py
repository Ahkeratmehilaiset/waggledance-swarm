"""Public synthetic runner probes, never the sealed M0 corpus."""
import io
import importlib.machinery
import os
from pathlib import Path
import unittest

import m0_synthetic_runner as m0


class BoundaryTests(unittest.TestCase):
    def policy(self):
        return m0.Boundary(
            source_root="C:/frozen", source_files={"C:/frozen/allowed.py"},
            audit_root="C:/audit/run", runtime_roots={"C:/python"},
            extra_files={"C:/harness/test_public.py"})

    def test_frozen_collection_has_three_files_not_directories(self):
        """MUTANT_DIRECTORY_DISCOVERY."""
        got = m0.collection_paths("C:/frozen", "C:/harness/test_public.py")
        self.assertEqual(got, ["C:/frozen/sandbox/mind/remember/test_remember_v2.py",
                               "C:/frozen/sandbox/mind/remember/test_remember_v21.py",
                               "C:/harness/test_public.py"])

    def test_collection_filter_precedes_sibling_conftest_and_venv_probes(self):
        """MUTANT_LATE_DESELECT_AFTER_IMPORT."""
        paths = m0.collection_paths("C:/frozen", "C:/harness/test_public.py")
        self.assertTrue(m0.collection_route(paths[0], paths))
        self.assertTrue(m0.collection_route("C:/frozen/sandbox/mind/remember", paths))
        for path in ("C:/frozen/conftest.py", "C:/frozen/sandbox/mind/test_join_card_delivery.py",
                     "C:/frozen/sandbox/mind/step2/test_step2.py", "C:/harness/sibling.py"):
            with self.subTest(path=path):
                self.assertFalse(m0.collection_route(path, paths))

    def test_drive_and_device_aliases_are_rejected_before_os_calls(self):
        """MUTANT_F_DRIVE_ALIAS."""
        for value in ("F:/absent", "f:\\private", "F:relative", "\\\\?\\F:\\private",
                      "\\\\server\\share", "\\\\.\\C:", "\\Device\\HarddiskVolume1"):
            with self.subTest(value=value), self.assertRaises(m0.Refused):
                self.policy().check(value, "open")

    def test_unlisted_repo_content_is_not_readable(self):
        """MUTANT_REPO_READ_FALLTHROUGH."""
        p = self.policy()
        p.check("C:/frozen/allowed.py", "open")
        for value in ("C:/frozen/.git/config", "C:/frozen/cases.json", "C:/Users/u/.claude/memory.md"):
            with self.subTest(value=value), self.assertRaises(m0.Refused):
                p.check(value, "open")

    def test_source_and_runtime_are_read_only(self):
        """MUTANT_SOURCE_WRITE_ALLOWED."""
        p = self.policy()
        p.check("C:/audit/run/new.json", "open", write=True)
        for value in ("C:/frozen/allowed.py", "C:/python/package.py", "C:/audit/run-sibling/x"):
            with self.subTest(value=value), self.assertRaises(m0.Refused):
                p.check(value, "open", write=True)

    def test_only_the_bare_null_sink_is_a_nonfile_write_exception(self):
        """MUTANT_NULL_SINK_BECOMES_DEVICE_PREFIX_ALLOWLIST."""
        p = self.policy()
        p.check("nul", "open", write=True)
        for value in ("nul/escape", "\\\\.\\pipe\\escape", "\\\\?\\F:\\escape"):
            with self.subTest(value=value), self.assertRaises(m0.Refused):
                p.check(value, "open", write=True)

    def test_parent_metadata_is_not_file_read_authority(self):
        """MUTANT_PARENT_METADATA_PROMOTED_TO_READ."""
        p = self.policy()
        p.check("C:/frozen", "stat")
        p.check("C:/__init__.py", "stat")
        with self.assertRaises(m0.Refused):
            p.check("C:/frozen", "open")
        with self.assertRaises(m0.Refused):
            p.check("C:/__init__.py", "open")

    def test_integration_and_conftest_never_become_allowed_dependencies(self):
        """MUTANT_EXCLUDED_IMPORTED_INDIRECTLY."""
        for relative in (*m0.EXCLUDED, "conftest.py", "sandbox/mind/conftest.py"):
            value = "C:/frozen/" + relative
            p = m0.Boundary("C:/frozen", {value}, "C:/audit/run", {"C:/python"}, set())
            with self.subTest(relative=relative), self.assertRaises(m0.Refused):
                p.check(value, "open")

    def test_ref_grader_uses_each_questions_own_candidates(self):
        """MUTANT_UNION_ALLOWLIST."""
        m0.check_refs(["lesson_a.md"], {"lesson_a.md"})
        with self.assertRaises(m0.Refused):
            m0.check_refs(["lesson_a.md"], {"lesson_b.md"})

    def test_whole_rendered_input_is_checked_not_just_answer_refs(self):
        """MUTANT_REF_ONLY_LEAK_CHECK."""
        m0.check_rendered("[1] lesson_a.md normal text", {"lesson_a.md"}, {"hidden_canary"})
        for rendered in ("lesson_a.md [CLAIMED SUPERSEDED by lesson_hidden.md]", "lesson_a.md hidden_canary"):
            with self.subTest(rendered=rendered), self.assertRaises(m0.Refused):
                m0.check_rendered(rendered, {"lesson_a.md"}, {"hidden_canary"})

    def test_active_boundary_denies_open_exists_and_directory_access(self):
        """MUTANT_STAT_BYPASS; this also executes under the actual three-file collection."""
        if m0.ACTIVE is None:
            self.skipTest("actual boundary checked by explicit runner child")
        probes = (lambda: open("F:/m0-synthetic-never-read.txt"),
                  lambda: io.open("F:/m0-synthetic-never-read.txt"),
                  lambda: Path("F:/m0-synthetic-never-read.txt").exists(),
                  lambda: os.scandir("F:/m0-synthetic-never-read"),
                  lambda: Path(m0.ACTIVE.source_root, "cases.json").read_bytes())
        for probe in probes:
            with self.assertRaises(m0.Refused):
                probe()
        # MUTANT_EXCLUDED_DEPENDENCY_IMPORT / MUTANT_HASH_READ_AS_IMPORT.
        for relative in (*m0.EXCLUDED, "conftest.py", "sandbox/mind/remember/test_remember_v3.py"):
            loader = importlib.machinery.SourceFileLoader("synthetic_forbidden", str(Path(m0.ACTIVE.source_root, relative)))
            with self.subTest(relative=relative), self.assertRaises(m0.Refused):
                loader.get_code("synthetic_forbidden")


if __name__ == "__main__":
    unittest.main()
