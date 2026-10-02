# SPDX-License-Identifier: BUSL-1.1
"""Tests for the DORMANT advisory tools/bridge_leaf_reuse.py.

Positives run on real immutable reviewed objects of this repository (skipped when a
shallow clone lacks them); negatives run on a synthetic in-memory object store whose
objects are real git encodings (sha1 of "<type> <size>\\0<data>")."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import bridge_leaf_reuse as L

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_leaf_reuse.py"

D586 = "d5864fdd55129a5cf36f19c8347892f4d2f9ef00"
R1276 = "1276dc9c165911927a2fe2896ad30f9d4525df27"
C892 = "c892ae9852adbf7d0b3bc67d7843f4cb5a602e9c"
A95 = "95a83b4daceb8062ef93867d7fa5efe826fd2f43"
B5B = "5b294848626ec25e74fe498f65704a2b1f278040"
E83 = "e83d6a150443d096f964c3a2ba5f19f2aebfd5b2"
CFA = "cfa59a02ada1cb576e4fcf7940f59d9e4876133a"
E3E = "e3e05c9b2c9e99fcb22e70e3b8816bfa16c23ac5"
CLS = ".agent-bridge/bin/BridgeEventClassifier.ps1"
SEL = ".agent-bridge/bin/Get-BridgeNextAction.ps1"
CON = ".agent-bridge/bin/BridgeRequestContract.ps1"
REAL = [D586, R1276, C892, A95, B5B, E83, CFA, E3E]


# ---------------------------------------------------------------- synthetic store
class Store:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.fail = set()

    def put(self, kind, data):
        oid = hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).hexdigest()
        self.objects[oid] = (kind, data)
        return oid

    def blob(self, data):
        return self.put("blob", data)

    def tree(self, entries):
        """entries: {name: (mode, oid)}; nested dicts become subtrees."""
        raw = b""
        for name in sorted(entries):
            value = entries[name]
            if isinstance(value, dict):
                value = ("40000", self.tree(value))
            raw += value[0].encode() + b" " + name.encode() + b"\0" + bytes.fromhex(value[1])
        return self.put("tree", raw)

    def commit(self, files, parents=()):
        """files: {path: blob_oid or (mode, oid)}."""
        nested = {}
        for path, value in files.items():
            node = nested
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value if isinstance(value, tuple) else ("100644", value)
        body = b"tree " + self.tree(nested).encode() + b"\n"
        for p in parents:
            body += b"parent " + p.encode() + b"\n"
        body += b"author t <t> 0 +0000\ncommitter t <t> 0 +0000\n\nmsg\n"
        return self.put("commit", body)

    def read(self, oid):
        self.reads.append(oid)
        if oid in self.fail:
            raise L.ReaderError("injected read failure")
        return self.objects.get(oid)


def chain_repo(contents, path="f.py", extra=None):
    """Linear history: commit i holds path=contents[i] (None = absent). Returns (store, [commits])."""
    s, commits, parent = Store(), [], ()
    for data in contents:
        files = dict(extra or {})
        if data is not None:
            files[path] = s.blob(data)
        c = s.commit(files, parent)
        commits.append(c)
        parent = (c,)
    return s, commits


# ---------------------------------------------------------------- real immutable objects
@pytest.fixture(scope="module")
def real():
    reader = L.GitObjectReader(ROOT)
    try:
        if any(reader.read(c) is None for c in REAL):
            pytest.skip("reviewed objects absent from this clone (shallow checkout)")
        yield reader
    finally:
        reader.close()


def test_real_c892_pair_onto_1276_reuses_the_contiguous_95a83_chain(real):
    r = L.assess(real, R1276, {CLS: C892, SEL: C892}, [A95, C892])
    assert r["paths"][CLS]["verdict"] == "REUSE" and r["paths"][CLS]["chain"] == [A95, C892]
    assert r["paths"][SEL]["verdict"] == "REUSE"
    assert r["dependency_check"]["status"] == "no_missing_found"
    assert r["dependency_check"]["closure"] == "not_proven"
    assert r["overall"] == L.REUSE_ALL and r["authority_effect"] == "none" and r["approval"] == "unknown"


def test_real_selector_without_its_classifier_is_a_missing_helper(real):
    r = L.assess(real, R1276, {SEL: C892}, [A95, C892])
    assert r["paths"][SEL]["verdict"] == "REUSE"
    assert {"leaf": SEL, "function": "Get-BridgeEventStatusText"} in r["dependency_check"]["missing"]
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_real_c892_pair_onto_installed_d586_is_base_drift(real):
    r = L.assess(real, D586, {CLS: C892, SEL: C892}, [A95, C892])
    assert r["paths"][SEL]["verdict"] == "BASE_DRIFT" and r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_real_same_file_binding_stack_reuses_only_with_the_full_chain(real):
    r = L.assess(real, R1276, {CON: B5B}, [E3E, CFA, E83, B5B])
    assert r["paths"][CON]["verdict"] == "REUSE" and r["paths"][CON]["chain"] == [E3E, CFA, E83, B5B]
    r = L.assess(real, R1276, {CON: B5B}, [CFA, E83, B5B])
    assert r["paths"][CON]["verdict"] == "BASE_DRIFT" and r["overall"] == L.DELTA_REVIEW_REQUIRED
    r = L.assess(real, R1276, {CON: B5B}, [E3E, E83, B5B])
    assert r["paths"][CON]["verdict"] == "CHAIN_BROKEN"


def test_real_extra_unreviewed_path_and_unknown_head(real):
    r = L.assess(real, R1276, {CLS: C892, SEL: C892}, [A95, C892], extra_paths=["tools/bridge_next_action.py"])
    assert r["paths"]["tools/bridge_next_action.py"]["verdict"] == "UNREVIEWED_EXTRA"
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED
    r = L.assess(real, R1276, {CLS: C892}, ["0" * 40])
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED and "not found" in r["error"]


def test_cli_reports_advisory_json_without_authority(tmp_path):
    reader = L.GitObjectReader(ROOT)
    try:
        if any(reader.read(c) is None for c in (R1276, A95, C892)):
            pytest.skip("reviewed objects absent from this clone")
    finally:
        reader.close()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"base": R1276, "leaves": {CLS: C892, SEL: C892}, "reviewed_heads": [A95, C892]}))
    p = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(ROOT), "--input-json", str(plan)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    report = json.loads(p.stdout)
    assert report["overall"] == L.REUSE_ALL and report["authority_effect"] == "none"
    text = p.stdout.lower()
    assert "approved" not in text and "accepted" not in text and "rco_pass" not in text
    plan.write_text(json.dumps({"base": R1276, "leaves": {}, "surprise": 1}))
    p = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(ROOT), "--input-json", str(plan)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 2


# ---------------------------------------------------------------- synthetic seam
def test_contiguous_chain_and_cycle_terminate():
    s, (c0, c1, c2, c3) = chain_repo([b"a\n", b"b\n", b"c\n", b"a\n"])
    r = L.assess(s, c0, {"f.py": c2}, [c1, c2])
    assert r["paths"]["f.py"]["verdict"] == "REUSE" and r["paths"]["f.py"]["chain"] == [c1, c2]
    # c3 goes c -> a: reviewed cycle a -> b -> c -> a; a target outside the cycle still terminates
    other = s.commit({"f.py": s.blob(b"z\n")}, (c0,))
    r = L.assess(s, c0, {"f.py": other}, [c1, c2, c3])
    assert r["paths"]["f.py"]["verdict"] == "TARGET_UNREVIEWED"
    r = L.assess(s, c1, {"f.py": c0}, [c1, c2, c3])
    assert r["paths"]["f.py"]["verdict"] == "REUSE" and r["paths"]["f.py"]["chain"] == [c2, c3]


def test_unchanged_path_is_not_reuse_and_all_unchanged_is_no_change():
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    r = L.assess(s, c0, {"f.py": c0}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "UNCHANGED" and r["overall"] == L.NO_CHANGE


def test_broken_chain_and_missing_transition():
    s, (c0, c1, c2, c3) = chain_repo([b"a\n", b"b\n", b"c\n", b"d\n"])
    r = L.assess(s, c0, {"f.py": c3}, [c1, c3])
    assert r["paths"]["f.py"]["verdict"] == "CHAIN_BROKEN" and r["overall"] == L.DELTA_REVIEW_REQUIRED
    r = L.assess(s, c0, {"f.py": c2}, [])
    assert r["paths"]["f.py"]["verdict"] == "UNREVIEWED"


def test_base_drift():
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    drifted = s.commit({"f.py": s.blob(b"a2\n")})
    r = L.assess(s, drifted, {"f.py": c1}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "BASE_DRIFT"


def test_new_file_deletion_absent_path_and_root_head():
    s, (c0, c1, c2) = chain_repo([None, b"new\n", None], extra={"keep.txt": "0" * 40})
    r = L.assess(s, c0, {"f.py": c1}, [c1])
    assert r["paths"]["f.py"]["base_entry"] == L.ABSENT and r["paths"]["f.py"]["verdict"] == "REUSE"
    r = L.assess(s, c1, {"f.py": c2}, [c2])
    assert r["paths"]["f.py"]["target_entry"] == L.ABSENT and r["paths"]["f.py"]["verdict"] == "REUSE"
    r = L.assess(s, c1, {"f.py": c2}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "BASE_DRIFT"
    r = L.assess(s, c0, {"nope.py": c1}, [c1])
    assert r["paths"]["nope.py"]["verdict"] == "PATH_NOT_FOUND" and r["overall"] == L.DELTA_REVIEW_REQUIRED
    root = s.commit({"f.py": s.blob(b"new\n"), "keep.txt": "0" * 40})
    r = L.assess(s, c0, {"f.py": root}, [root])
    assert r["paths"]["f.py"]["verdict"] == "REUSE"


def test_mode_change_is_a_change_and_needs_its_own_review():
    s = Store()
    b = s.blob(b"x\n")
    c0 = s.commit({"f.sh": b})
    c1 = s.commit({"f.sh": ("100755", b)}, (c0,))
    r = L.assess(s, c0, {"f.sh": c1}, [])
    assert r["paths"]["f.sh"]["verdict"] == "UNREVIEWED"
    assert L.assess(s, c0, {"f.sh": c1}, [c1])["paths"]["f.sh"]["verdict"] == "REUSE"


def test_final_lf_only_is_a_delta_review_never_reuse():
    s, (c0, c1) = chain_repo([b"a\n", b"b"])
    lf = s.blob(b"b\n")
    r = L.assess(s, c0, {"f.py": {"blob": lf, "mode": "100644"}}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "FINAL_LF_ONLY" and r["overall"] == L.DELTA_REVIEW_REQUIRED
    crlf = s.blob(b"b\r\n")
    r = L.assess(s, c0, {"f.py": {"blob": crlf, "mode": "100644"}}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "TARGET_UNREVIEWED"


def test_bytes_not_replace_decoded_text_decide_identity():
    s, (c0, c1) = chain_repo([b"a\n", b"x\xff"])
    other = s.blob(b"x\xfe\n")  # replace-decodes to the reviewed text plus a newline
    r = L.assess(s, c0, {"f.py": {"blob": other, "mode": "100644"}}, [c1])
    assert r["paths"]["f.py"]["verdict"] == "TARGET_UNREVIEWED"


def test_proposed_blob_must_resolve_to_a_blob_object():
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    reviewed_blob = L._Objects(s).entry(c1, "f.py")[1]
    ok = L.assess(s, c0, {"f.py": {"blob": reviewed_blob, "mode": "100644"}}, [c1])
    assert ok["paths"]["f.py"]["verdict"] == "REUSE"
    tree_oid = L._Objects(s).commit(c1)[0]
    for spec in ({"blob": "1" * 40, "mode": "100644"}, {"blob": tree_oid, "mode": "100644"},
                 {"blob": reviewed_blob, "mode": "040000"}, {"blob": reviewed_blob}):
        r = L.assess(s, c0, {"f.py": spec}, [c1])
        assert r["paths"]["f.py"]["verdict"] == "TARGET_UNRESOLVED" and r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_gitlink_and_directory_entries_are_unsupported():
    s = Store()
    c0 = s.commit({"m": ("160000", "a" * 40)})
    c1 = s.commit({"m": ("160000", "b" * 40)}, (c0,))
    assert L.assess(s, c0, {"m": c1}, [c1])["paths"]["m"]["verdict"] == "UNSUPPORTED_ENTRY"
    d0 = s.commit({"d/x.py": s.blob(b"1")})
    d1 = s.commit({"d/x.py": s.blob(b"2")}, (d0,))
    assert L.assess(s, d0, {"d": d1}, [d1])["paths"]["d"]["verdict"] == "UNSUPPORTED_ENTRY"


@pytest.mark.parametrize("bad", ["c0ffee", "HEAD", "--all", "A" * 40, "0" * 39, "g" * 40, " " + "0" * 40, None, 7])
def test_non_exact_ids_fail_closed_before_any_object_read(bad):
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    for args in ((bad, {"f.py": c1}, [c1]), (c0, {"f.py": bad}, [c1]), (c0, {"f.py": c1}, [bad])):
        s.reads.clear()
        r = L.assess(s, *args)
        assert r["overall"] == L.DELTA_REVIEW_REQUIRED and "error" in r and s.reads == []


@pytest.mark.parametrize("bad", ["", "/f.py", "f.py/", "a//f.py", "a/./f.py", "../f.py", "a\\f.py", "f\x00.py", "f\n.py"])
def test_invalid_paths_fail_closed(bad):
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    assert "error" in L.assess(s, c0, {bad: c1}, [c1])
    assert "error" in L.assess(s, c0, {"f.py": c1}, [c1], extra_paths=[bad])


def test_pathspec_looking_paths_are_literal_tree_lookups():
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    r = L.assess(s, c0, {":(glob)*.py": c1, "*": c1}, [c1])
    assert {v["verdict"] for v in r["paths"].values()} == {"PATH_NOT_FOUND"}


def test_unknown_commit_wrong_type_unreadable_object_and_merge_head():
    s, (c0, c1) = chain_repo([b"a\n", b"b\n"])
    assert "not found" in L.assess(s, "e" * 40, {"f.py": c1}, [c1])["error"]
    tree_oid = L._Objects(s).commit(c1)[0]
    assert "is a tree" in L.assess(s, tree_oid, {"f.py": c1}, [c1])["error"]
    s.fail.add(c1)
    r = L.assess(s, c0, {"f.py": c1}, [c1])
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED and "ReaderError" in r["error"]
    s.fail.clear()
    side = s.commit({"f.py": s.blob(b"c\n")}, (c0,))
    merge = s.commit({"f.py": s.blob(b"m\n")}, (c1, side))
    assert "merge commit" in L.assess(s, c0, {"f.py": merge}, [merge])["error"]


def test_reader_command_failures_fail_closed(tmp_path):
    for reader in (L.GitObjectReader(tmp_path / "not-a-repo"), L.GitObjectReader(ROOT, git="git-not-installed-xyz")):
        with reader:
            r = L.assess(reader, "1" * 40, {"f.py": "2" * 40}, ["3" * 40])
        assert r["overall"] == L.DELTA_REVIEW_REQUIRED and "ReaderError" in r["error"]


def test_reader_argv_is_fixed_and_drops_inherited_git_variables(monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"], seen["env"] = argv, kw["env"]
            raise OSError("stop here")

    monkeypatch.setenv("GIT_DIR", "elsewhere")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "elsewhere")
    monkeypatch.setattr(L.subprocess, "Popen", FakePopen)
    with pytest.raises(L.ReaderError):
        L.GitObjectReader("repo").read("0" * 40)
    assert seen["argv"] == ["git", "--no-replace-objects", "-C", "repo", "cat-file", "--batch"]
    assert not [k for k in seen["env"] if k.upper().startswith("GIT_")]


# ---------------------------------------------------------------- PowerShell dependency approximation
HELPER = b"function Get-Helper { 'x' }\n"
CALLER = b"function Invoke-Caller { Get-Helper }\n"


def ps_history(store, caller=CALLER):
    base = store.commit({".agent-bridge/bin/C.ps1": store.blob(b"function Invoke-Caller { 1 }\n")})
    head = store.commit({".agent-bridge/bin/C.ps1": store.blob(caller),
                         ".agent-bridge/bin/H.ps1": store.blob(HELPER)}, (base,))
    return base, head


def test_paired_ps_leaves_close_and_caller_alone_is_missing():
    s = Store()
    base, head = ps_history(s)
    both = L.assess(s, base, {".agent-bridge/bin/C.ps1": head, ".agent-bridge/bin/H.ps1": head}, [head])
    assert both["overall"] == L.REUSE_ALL and both["dependency_check"]["closure"] == "not_proven"
    alone = L.assess(s, base, {".agent-bridge/bin/C.ps1": head}, [head])
    assert alone["dependency_check"]["missing"] == [{"leaf": ".agent-bridge/bin/C.ps1", "function": "Get-Helper"}]
    assert alone["overall"] == L.DELTA_REVIEW_REQUIRED


def test_ps_function_names_match_case_insensitively():
    s = Store()
    base, head = ps_history(s, caller=b"function Invoke-Caller { get-HELPER }\n")
    r = L.assess(s, base, {".agent-bridge/bin/C.ps1": head}, [head])
    assert r["dependency_check"]["missing"] == [{"leaf": ".agent-bridge/bin/C.ps1", "function": "Get-Helper"}]


def test_malformed_utf8_ps_leaf_makes_dependencies_unknown_not_clean():
    s = Store()
    base, head = ps_history(s, caller=b"function Invoke-Caller { Get-Helper }\n\xff\n")
    r = L.assess(s, base, {".agent-bridge/bin/C.ps1": head, ".agent-bridge/bin/H.ps1": head}, [head])
    assert r["paths"][".agent-bridge/bin/C.ps1"]["verdict"] == "REUSE"
    assert r["dependency_check"]["status"] == "unknown" and r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_no_gate_or_runtime_consumer_imports_the_tool():
    hits = []
    for folder in ("tools", "waggledance", ".agent-bridge"):
        for path in (ROOT / folder).rglob("*"):
            if path.suffix.lower() in (".py", ".ps1", ".psm1") and path.name != "bridge_leaf_reuse.py":
                if "bridge_leaf_reuse" in path.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(path.relative_to(ROOT)))
    assert hits == []
