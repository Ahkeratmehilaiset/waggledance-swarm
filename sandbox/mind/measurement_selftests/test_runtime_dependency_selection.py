"""AUTHOR synthetic-only selection fixtures; no installed/product/model/data access."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

BINDING = "b" * 64
ENV = {"sys_platform": "win32", "python_version": "3.13", "platform_system": "Windows"}
SUBJECT = None
LEGACY = False


def load_subject(mutant=None):
    path = Path(__file__).with_name("runtime_dependency_selection.py")
    if not path.is_file():
        # Proposed custody layout: measurement_selftests vs measurement_tools.
        path = Path(__file__).parent.parent / "measurement_tools/runtime_dependency_selection.py"
    spec = importlib.util.spec_from_file_location("runtime_selection_candidate", path)
    module = importlib.util.module_from_spec(spec)
    if mutant:
        replacements = {
            "MUTANT_WINDOWS_CASE_COLLISION_FIRST_OWNER_WINS": ('if owns(module, p, windows)', 'if owns(module, p, False)'),
            "MUTANT_WINDOWS_CASE_DUPLICATE_ACCEPTED": ('record_keys = [record_identity(p, windows) for p in paths]', 'record_keys = paths'),
            "MUTANT_IGNORE_CAPABILITY_ROOTS": ('enabled = sequence(capabilities, "MALFORMED_CAPABILITIES", 2048)', 'enabled = []'),
            "MUTANT_AMBIGUOUS_OWNER_FIRST_WINS": ('need(len(owners) == 1, "AMBIGUOUS_OWNER")', 'need(True, "AMBIGUOUS_OWNER")'),
            "MUTANT_UNAPPROVED_PROOF_ACCEPTED": ('need(proof_sha in approved, "UNAPPROVED_PROOF")', 'need(True, "UNAPPROVED_PROOF")'),
            "MUTANT_UNKNOWN_MARKER_ASSUMED_ACTIVE": ('        raise SelectionRefused("UNKNOWN_MARKER")\n    def walk', '        return "x"\n    def walk'),
            "MUTANT_BLANKET_INSTALLED_EXPANSION": ('visited, selected = set(), set()', 'visited, selected = set(), set(registry)'),
            "MUTANT_PROOF_BYTES_NOT_DIGEST_BOUND": ('need(sha256_value({k: v for k, v in proof.items() if k != "sha256"}) == proof["sha256"], "PROOF_DIGEST_MISMATCH")', 'need(True, "PROOF_DIGEST_MISMATCH")'),
        }
        old, new = replacements[mutant]
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise ValueError("mutant_target_not_unique")
        # Only this candidate, in this test process; no file mutation or child.
        exec(compile(source.replace(old, new), str(path), "exec"), module.__dict__)
    else:
        spec.loader.exec_module(module)
    return module


def hashed(body):
    return hashlib.sha256((json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def dist(name, requires=(), record=(), extras=()):
    return {"name": name, "version": "1.0", "requires": list(requires), "record": list(record),
            "extras": list(extras), "metadata_sha256": "d" * 64, "record_sha256": "e" * 64}


def req(name, marker="", extras=()):
    return {"name": name, "marker": marker, "extras": list(extras)}


def fixture():
    rows = [dist("base", [req("core"), req("pytest", 'extra == "dev"'),
                           req("linux-only", 'sys_platform == "linux"')], ["base/__init__.py"], ["dev"]),
            dist("core", record=["core/__init__.py"]),
            dist("attrs", [req("helper")], ["attr/__init__.py", "attr/_compat.py", "attrs/__init__.py"]),
            dist("helper", record=["helper.py"]), dist("pytest", record=["pytest/__init__.py"]),
            dist("unrelated-installed", record=["unrelated.py"])]
    proof = {"binding": BINDING, "module": "attr._compat", "distribution": "attrs",
             "kind": "observed_import", "evidence_sha256": "a" * 64}
    proof["sha256"] = hashed(proof)
    cap = {"module": "attr._compat", "distribution": "attrs", "proof_sha256": proof["sha256"]}
    return rows, cap, proof


def selected(rows=None, caps=True, proofs=None, approved=None, **changes):
    data, cap, proof = fixture()
    if rows is None:
        rows = data
    args = {"distributions": rows, "seeds": ["base"], "capabilities": [cap] if caps else [],
            "proofs": [proof] if proofs is None else proofs,
            "approved_proof_hashes": [proof["sha256"]] if approved is None else approved,
            "binding": BINDING, "environment": ENV}
    args.update(changes)
    if LEGACY:
        # Frozen declared-only control: the valid base graph selects base/core,
        # but has no explicit capability input or alias-owner expansion.
        return {"selected": ["base", "core"], "provenance": []}
    return SUBJECT.select_runtime_dependencies(**args)


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global SUBJECT
        if SUBJECT is None and not LEGACY:
            SUBJECT = load_subject()

    def refused(self, code, call):
        with self.assertRaises(SUBJECT.SelectionRefused) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_MUTANT_DECLARED_ONLY_OMITS_PROVEN_ROOT(self):
        self.assertEqual(selected()["selected"], ["attrs", "base", "core", "helper"])

    def test_MUTANT_ALIAS_NAME_GUESSED_NOT_RECORD_OWNED(self):
        result = selected()
        self.assertIn("attrs", result["selected"])
        self.assertNotIn("attr", result["selected"])
        evidence = next(p for p in result["provenance"] if p["kind"] == "capability")
        self.assertEqual(evidence["record_path"], "attr/_compat.py")
        self.assertEqual(evidence["distribution"], "attrs")

    def test_MUTANT_BLANKET_INSTALLED_EXPANSION(self):
        self.assertEqual(selected(caps=False)["selected"], ["base", "core"])

    def test_MUTANT_FALSE_DEV_PLATFORM_EDGES_INCLUDED(self):
        names = selected()["selected"]
        self.assertNotIn("pytest", names)
        self.assertNotIn("linux-only", names)
        self.assertNotIn("unrelated-installed", names)

    def test_MUTANT_REQUIRED_DEPENDENCY_SKIPPED(self):
        rows, _, _ = fixture()
        rows[1]["requires"] = [req("missing")]
        self.refused("MISSING_DISTRIBUTION", lambda: selected(rows))

    def test_MUTANT_CAPABILITY_OWNER_MISSING_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[2]["record"] = []
        self.refused("MISSING_OWNER", lambda: selected(rows))

    def test_MUTANT_AMBIGUOUS_OWNER_FIRST_WINS(self):
        rows, _, _ = fixture()
        rows.append(dist("collision", record=["attr/_compat.py"]))
        self.refused("AMBIGUOUS_OWNER", lambda: selected(rows))

    def test_MUTANT_WRONG_OWNER_LABEL_ACCEPTED(self):
        rows, cap, proof = fixture()
        cap["distribution"] = "core"
        self.refused("PROOF_BINDING_MISMATCH", lambda: selected(rows, capabilities=[cap]))

    def test_MUTANT_UNAPPROVED_PROOF_ACCEPTED(self):
        self.refused("UNAPPROVED_PROOF", lambda: selected(approved=[]))

    def test_MUTANT_UNKNOWN_PROOF_ACCEPTED(self):
        self.refused("MISSING_PROOF", lambda: selected(proofs=[]))

    def test_MUTANT_PROOF_BYTES_NOT_DIGEST_BOUND(self):
        _, _, proof = fixture()
        proof["evidence_sha256"] = "c" * 64
        self.refused("PROOF_DIGEST_MISMATCH", lambda: selected(proofs=[proof]))

    def test_MUTANT_FOREIGN_BINDING_ACCEPTED(self):
        self.refused("PROOF_BINDING_MISMATCH", lambda: selected(binding="f" * 64))

    def test_MUTANT_MALFORMED_PROOF_ACCEPTED(self):
        _, _, proof = fixture()
        proof["unexpected"] = "authority"
        self.refused("MALFORMED_PROOF", lambda: selected(proofs=[proof]))

    def test_MUTANT_UNKNOWN_MARKER_ASSUMED_ACTIVE(self):
        rows, _, _ = fixture()
        rows[0]["requires"].append(req("core", 'unknown_platform == "x"'))
        self.refused("UNKNOWN_MARKER", lambda: selected(rows))

    def test_MUTANT_UNSUPPORTED_MARKER_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[0]["requires"].append(req("core", 'python_version ~= "3.13"'))
        self.refused("UNKNOWN_MARKER", lambda: selected(rows))

    def test_MUTANT_NON_STRING_MARKER_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[0]["requires"][0]["marker"] = True
        self.refused("MALFORMED_REQUIREMENT", lambda: selected(rows))

    def test_MUTANT_DUPLICATE_DISTRIBUTION_LAST_WINS(self):
        rows, _, _ = fixture()
        rows.append(dist("BASE", record=["other.py"]))
        self.refused("AMBIGUOUS_DISTRIBUTION", lambda: selected(rows))

    def test_MUTANT_RECORD_ESCAPE_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[2]["record"].append("../private.py")
        self.refused("MALFORMED_RECORD_PATH", lambda: selected(rows))

    def test_MUTANT_NAME_INJECTION_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[2]["name"] = "attrs;import os"
        self.refused("MALFORMED_NAME", lambda: selected(rows))

    def test_MUTANT_UNKNOWN_INPUT_FIELD_IGNORED(self):
        rows, _, _ = fixture()
        rows[2]["installed_available"] = True
        self.refused("MALFORMED_DISTRIBUTION", lambda: selected(rows))

    def test_MUTANT_NONDETERMINISTIC_ORDER(self):
        rows, _, _ = fixture()
        a = selected(rows)
        b = selected(list(reversed(rows)))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_MUTANT_CALLER_INPUT_MUTATED(self):
        rows, cap, proof = fixture()
        before = copy.deepcopy((rows, cap, proof))
        selected(rows, capabilities=[cap], proofs=[proof])
        self.assertEqual((rows, cap, proof), before)

    def test_MUTANT_VALID_DECLARED_EXTRAS_DROPPED(self):
        rows, _, _ = fixture()
        rows[0]["requires"].append(req("core", extras=["feature"]))
        rows[1]["extras"] = ["feature"]
        rows[1]["requires"] = [req("helper", 'extra == "feature"')]
        self.assertEqual(selected(rows, caps=False)["selected"], ["base", "core", "helper"])

    def test_MUTANT_UNKNOWN_EXTRA_SILENTLY_IGNORED(self):
        rows, _, _ = fixture()
        rows[0]["requires"].append(req("core", extras=["unknown"]))
        self.refused("UNKNOWN_EXTRA", lambda: selected(rows))

    def test_MUTANT_VERSION_ZERO_PADDING_WRONG(self):
        rows, _, _ = fixture()
        rows[0]["requires"].append(req("helper", 'python_version >= "3.13.0"'))
        self.assertEqual(selected(rows, caps=False)["selected"], ["base", "core", "helper"])

    def test_MUTANT_DECLARED_CYCLE_LOOPS(self):
        rows, _, _ = fixture()
        rows[1]["requires"] = [req("base")]
        self.assertEqual(selected(rows, caps=False)["selected"], ["base", "core"])

    def test_MUTANT_EXTENSION_ALIAS_UNRECOGNIZED(self):
        rows, cap, proof = fixture()
        rows[2]["record"] = ["attr/_compat.cp313-win_amd64.pyd"]
        self.assertIn("attrs", selected(rows)["selected"])

    def test_MUTANT_AUTHORITY_OR_COMPLETENESS_CLAIMED(self):
        result = selected()
        self.assertEqual(result["authority"], "none")
        self.assertIs(result["dynamic_closure_complete"], False)
        self.assertIs(result["version_constraints_validated"], False)
        self.assertIs(result["runtime_ready"], False)

    def test_MUTANT_MALFORMED_CAPABILITY_RAW_EXCEPTION(self):
        self.refused("MALFORMED_CAPABILITY", lambda: selected(capabilities=[{"module": {1}, "distribution": "attrs", "proof_sha256": "a" * 64}]))

    def test_MUTANT_CAPABILITY_UNKNOWN_FIELD_IGNORED(self):
        _, cap, _ = fixture()
        cap["enabled_by_availability"] = True
        self.refused("MALFORMED_CAPABILITY", lambda: selected(capabilities=[cap]))

    def test_MUTANT_DUPLICATE_PROOF_ACCEPTED(self):
        _, _, proof = fixture()
        self.refused("AMBIGUOUS_PROOF", lambda: selected(proofs=[proof, proof]))

    def test_MUTANT_WRONG_RECORD_OWNER_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[2]["record"] = []
        rows[1]["record"].append("attr/_compat.py")
        self.refused("OWNER_MISMATCH", lambda: selected(rows))

    def test_MUTANT_ENVIRONMENT_EXTRA_OVERRIDE_ACCEPTED(self):
        self.refused("MALFORMED_ENVIRONMENT", lambda: selected(environment=dict(ENV, extra="dev")))

    def test_MUTANT_NONDETERMINISTIC_MULTIPLE_CAPABILITIES(self):
        rows, cap, proof = fixture()
        other = {"binding": BINDING, "module": "unrelated", "distribution": "unrelated-installed",
                 "kind": "explicit_capability", "evidence_sha256": "9" * 64}
        other["sha256"] = hashed(other)
        cap2 = {"module": "unrelated", "distribution": "unrelated-installed", "proof_sha256": other["sha256"]}
        a = selected(rows, capabilities=[cap, cap2], proofs=[proof, other], approved=[proof["sha256"], other["sha256"]])
        b = selected(list(reversed(rows)), capabilities=[cap2, cap], proofs=[other, proof], approved=[other["sha256"], proof["sha256"]])
        self.assertEqual(a, b)

    def test_MUTANT_WINDOWS_CASE_COLLISION_FIRST_OWNER_WINS(self):
        rows, _, _ = fixture()
        rows.append(dist("collision", record=["ATTR/_COMPAT.PY"]))
        self.refused("AMBIGUOUS_OWNER", lambda: selected(rows))

    def test_MUTANT_WINDOWS_CASE_DUPLICATE_ACCEPTED(self):
        rows, _, _ = fixture()
        rows[2]["record"].append("ATTR/_COMPAT.PY")
        self.refused("MALFORMED_RECORD_PATH", lambda: selected(rows))

    def test_MUTANT_POSIX_CASE_COLLISION_FALSE_AMBIGUITY(self):
        rows, _, _ = fixture()
        rows.extend([dist("collision", record=["ATTR/_COMPAT.PY"]), dist("linux-only")])
        result = selected(rows, environment=dict(ENV, sys_platform="linux", platform_system="Linux"))
        self.assertEqual(result["selected"], ["attrs", "base", "core", "helper", "linux-only"])

    def test_MUTANT_POSIX_CASE_DISTINCT_RECORDS_REJECTED(self):
        rows, _, _ = fixture()
        rows[2]["record"].append("ATTR/_COMPAT.PY")
        rows.append(dist("linux-only"))
        result = selected(rows, environment=dict(ENV, sys_platform="linux", platform_system="Linux"))
        capability = next(p for p in result["provenance"] if p["kind"] == "capability")
        self.assertEqual(capability["ownership_record_paths"], ["attr/_compat.py"])
        self.assertEqual(result["selected"], ["attrs", "base", "core", "helper", "linux-only"])

    def test_MUTANT_WINDOWS_ORIGINAL_RECORD_SPELLING_LOST(self):
        rows, _, _ = fixture()
        rows[2]["record"] = ["ATTR/_COMPAT.PY"]
        result = selected(rows)
        capability = next(p for p in result["provenance"] if p["kind"] == "capability")
        self.assertEqual(capability["record_path"], "ATTR/_COMPAT.PY")
        self.assertEqual(capability["ownership_record_paths"], ["ATTR/_COMPAT.PY"])
        self.assertEqual(result["selected"], ["attrs", "base", "core", "helper"])

    def test_MUTANT_WINDOWS_EXTENSION_CASE_NOT_RECOGNIZED(self):
        rows, _, _ = fixture()
        rows[2]["record"] = ["ATTR/_COMPAT.CP313-WIN_AMD64.PYD"]
        self.assertEqual(selected(rows)["selected"], ["attrs", "base", "core", "helper"])


def main():
    global SUBJECT, LEGACY
    parser = argparse.ArgumentParser()
    parser.add_argument("--declared-only-control", action="store_true")
    parser.add_argument("--mutant")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    LEGACY = args.declared_only_control
    if not LEGACY:
        SUBJECT = load_subject(args.mutant)
    names = args.names or ["SelectionTests"]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names, sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
