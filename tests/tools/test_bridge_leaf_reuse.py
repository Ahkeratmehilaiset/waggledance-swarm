# SPDX-License-Identifier: BUSL-1.1
"""Tests for the DORMANT advisory tools/bridge_leaf_reuse.py.

Positives run on real immutable reviewed objects of this repository (skipped when a
shallow clone lacks them); negatives run on a synthetic in-memory object store whose
objects are real git encodings (sha1 of "<type> <size>\\0<data>")."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


# ---------------------------------------------------------------- lost definitions and complete names (RCO1 9132172d)
LIB, USE, OTHER = ".agent-bridge/bin/lib.ps1", ".agent-bridge/bin/use.ps1", ".agent-bridge/bin/other.ps1"
PS_BASE = {LIB: b"function Get-Foo { 1 }\n", USE: b"function Invoke-Use { Get-Foo }\n"}


def ps_commit(store, files, *parents):
    return store.commit({path: store.blob(data) for path, data in files.items()}, parents)


def dep_report(store, base, leaves, heads):
    r = L.assess(store, base, leaves, heads)
    assert (r["authority_effect"], r["approval"], r["review_evidence"]) == ("none", "unknown", "caller_supplied_unauthenticated")
    assert "error" not in r, r.get("error")
    assert r["dependency_check"]["closure"] == "not_proven"
    return r


def test_lost_definition_removal_leaf_with_unchanged_caller_is_missing_never_reuse_all():
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, {LIB: b"function Get-Other { 2 }\n", USE: b"function Invoke-Use { Get-Other }\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["paths"][LIB]["verdict"] == "REUSE"
    assert r["dependency_check"]["status"] == "missing" and r["overall"] == L.DELTA_REVIEW_REQUIRED
    assert {"caller": USE, "function": "Get-Foo"} in r["dependency_check"]["missing"]


def test_lost_definition_deletion_leaf_with_unchanged_caller_is_missing():
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, {USE: b"function Invoke-Use { 3 }\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["paths"][LIB]["target_entry"] == L.ABSENT
    assert r["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Foo"}]
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_lost_definition_rename_leaf_alone_is_missing_the_old_name():
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, {LIB: b"function Get-Bar { 1 }\n", USE: b"function Invoke-Use { Get-Bar }\n"}, base)
    assert dep_report(s, base, {LIB: head}, [head])["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Foo"}]


def test_lost_definition_called_from_a_case_variant_is_missing():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Foo { 1 }\n", USE: b"GET-FOO\n"})
    head = ps_commit(s, {LIB: b"function Get-Other { 1 }\n", USE: b"Get-Other\n"}, base)
    assert dep_report(s, base, {LIB: head}, [head])["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Foo"}]


def test_leaf_missing_a_reviewed_helper_keeps_the_leaf_key():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Old {}\n", USE: b"Get-Old\n"})
    helper = ps_commit(s, {LIB: b"function Get-Old {}\nfunction Get-New {}\n", USE: b"Get-Old\n"}, base)
    caller = ps_commit(s, {LIB: b"function Get-Old {}\nfunction Get-New {}\n", USE: b"Get-New\n"}, helper)
    r = dep_report(s, base, {USE: caller}, [helper, caller])
    assert r["dependency_check"]["missing"] == [{"leaf": USE, "function": "Get-New"}]


def test_complete_reviewed_pair_reuses():
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, {LIB: b"function Get-Other { 2 }\n", USE: b"function Invoke-Use { Get-Other }\n"}, base)
    r = dep_report(s, base, {LIB: head, USE: head}, [head])
    assert r["overall"] == L.REUSE_ALL
    assert r["dependency_check"]["status"] == "no_missing_found" and r["dependency_check"]["missing"] == []


def test_no_change_stays_no_change_with_a_clean_dependency_check():
    s = Store()
    base = ps_commit(s, PS_BASE)
    r = dep_report(s, base, {LIB: base, USE: base}, [])
    assert r["overall"] == L.NO_CHANGE and r["dependency_check"]["status"] == "no_missing_found"


def test_deletion_of_an_uncalled_definition_reuses():
    s = Store()
    base = ps_commit(s, dict(PS_BASE, **{OTHER: b"function Get-Unused { 0 }\n"}))
    head = ps_commit(s, PS_BASE, base)
    r = dep_report(s, base, {OTHER: head}, [head])
    assert r["overall"] == L.REUSE_ALL and r["dependency_check"]["status"] == "no_missing_found"


def test_moving_a_definition_between_composed_files_reuses_but_moving_it_out_alone_is_missing():
    s = Store()
    base = ps_commit(s, dict(PS_BASE, **{OTHER: b"# empty\n"}))
    head = ps_commit(s, {LIB: b"# moved\n", USE: PS_BASE[USE], OTHER: b"function Get-Foo { 1 }\n"}, base)
    both = dep_report(s, base, {LIB: head, OTHER: head}, [head])
    assert both["overall"] == L.REUSE_ALL and both["dependency_check"]["missing"] == []
    alone = dep_report(s, base, {LIB: head}, [head])
    assert alone["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Foo"}]


def test_three_part_helper_missing_is_detected():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Old {}\n", USE: b"Get-Old\n"})
    helper = ps_commit(s, {LIB: b"function Get-Old {}\nfunction Get-Bridge-New {}\n", USE: b"Get-Old\n"}, base)
    caller = ps_commit(s, {LIB: b"function Get-Old {}\nfunction Get-Bridge-New {}\n", USE: b"Get-Bridge-New\n"}, helper)
    r = dep_report(s, base, {USE: caller}, [helper, caller])
    assert r["dependency_check"]["missing"] == [{"leaf": USE, "function": "Get-Bridge-New"}]


@pytest.mark.parametrize("define, lost_call", [
    (b"function script:Get-Helper { }\n", b"Get-Helper\n"),
    (b"function GLOBAL:Get-Helper{ }\n", b"get-helper\n"),
    (b"  filter Get-Helper { $_ }\n", b"x | Get-Helper\n"),
    (b"FUNCTION private:Get-Helper-Two-Three { }\n", b"& Get-Helper-Two-Three\n"),
    (b"function Get-Foo_Bar { }\n", b"Get-Foo_Bar\n"),
])
def test_scope_prefix_filter_case_and_underscore_definitions_are_tracked(define, lost_call):
    s = Store()
    base = ps_commit(s, {LIB: define, USE: lost_call})
    head = ps_commit(s, {LIB: b"# gone\n", USE: b"# updated\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert len(r["dependency_check"]["missing"]) == 1 and r["overall"] == L.DELTA_REVIEW_REQUIRED, r["dependency_check"]


@pytest.mark.parametrize("survivor_ref", [b"Get-Bridge-New\n", b"Get-BridgeX\n", b"My-Get-Bridge\n", b"Get-Bridge-\n",
                                          b"xGet-Bridge\n", b"_Get-Bridge\n", b"9Get-Bridge\n", b"$a_Get-Bridge\n"])
def test_a_lost_short_name_never_matches_inside_a_longer_name(survivor_ref):
    s = Store()
    keep = b"function Get-Bridge-New {}\nfunction Get-BridgeX {}\nfunction My-Get-Bridge {}\n"
    base = ps_commit(s, {LIB: b"function Get-Bridge {}\n" + keep, USE: survivor_ref})
    head = ps_commit(s, {LIB: keep, USE: survivor_ref}, base)              # only Get-Bridge is lost
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["dependency_check"]["missing"] == [] and r["overall"] == L.REUSE_ALL, r["dependency_check"]


def test_a_lost_long_name_is_not_satisfied_by_its_two_part_prefix():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Bridge {}\nfunction Get-Bridge-New {}\n", USE: b"Get-Bridge-New\n"})
    head = ps_commit(s, {LIB: b"function Get-Bridge {}\n", USE: b"Get-Bridge\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Bridge-New"}]


def test_a_commented_definition_is_not_a_definition():
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, {LIB: b"# function Get-Foo { 1 }\n", USE: b"function Invoke-Use { 1 }\n"}, base)
    assert {"caller": USE, "function": "Get-Foo"} in dep_report(s, base, {LIB: head}, [head])["dependency_check"]["missing"]


def test_malformed_replaced_base_file_is_unknown():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Foo { 1 }\n\xff\n", USE: b"Get-Foo\n"})
    head = ps_commit(s, {LIB: b"function Get-Foo { 2 }\n", USE: b"Get-Foo\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["dependency_check"]["status"] == "unknown" and r["overall"] == L.DELTA_REVIEW_REQUIRED


# ---------------------------------------------------------------- definition-side guards (RCO2 c99b residuals)
def lost_foo_report(new_lib, extra=None):
    """Get-Foo is defined in LIB at the base and still called by the unchanged USE; the leaf replaces LIB."""
    s = Store()
    base = ps_commit(s, PS_BASE)
    head = ps_commit(s, dict({LIB: new_lib, USE: PS_BASE[USE]}, **(extra or {})), base)
    return dep_report(s, base, dict({LIB: head}, **{p: head for p in (extra or {})}), [head])


@pytest.mark.parametrize("new_lib", [
    b"<#\nfunction Get-Foo { 1 }\n#>\nfunction Get-Other { }\n",               # block comment
    b"<# a\n  function Get-Foo { 1 } #>\n",                                     # block comment, indented line
    b"$x = @'\nfunction Get-Foo { 1 }\n'@\n",                                  # single-quoted here-string, opener mid-line
    b"$x = @\"\r\nfunction Get-Foo { 1 }\r\n\"@\r\n",                           # double-quoted here-string, CRLF
    b"<#\nfunction Get-Foo { 1 }\n",                                            # unclosed block comment: the file does not parse
    b"$x = @'\nfunction Get-Foo { 1 }\n",                                       # unclosed here-string: the file does not parse
])
def test_a_definition_inside_a_block_comment_or_here_string_does_not_mask_a_loss(new_lib):
    r = lost_foo_report(new_lib)
    # the leaf's own commented/string mention also stays a reference (conservative), so check membership
    assert {"caller": USE, "function": "Get-Foo"} in r["dependency_check"]["missing"], r["dependency_check"]
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED


def test_a_kelvin_sign_definition_does_not_mask_the_ascii_name():
    # Python (?i) + casefold read U+212A as k; PowerShell does not resolve Get-Key to Get-<U+212A>ey.
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Key { 1 }\n", USE: b"Get-Key\n"})
    head = ps_commit(s, {LIB: "function Get-\u212aey { 1 }\n".encode("utf-8"), USE: b"Get-Key\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert {"caller": USE, "function": "Get-Key"} in r["dependency_check"]["missing"], r["dependency_check"]


def test_a_definition_moved_out_of_the_dependency_scope_is_missing_but_a_move_within_it_is_not():
    out = lost_foo_report(b"# moved\n", {"tools/moved.ps1": b"function Get-Foo { 1 }\n"})
    assert {"caller": USE, "function": "Get-Foo"} in out["dependency_check"]["missing"] and out["overall"] == L.DELTA_REVIEW_REQUIRED
    within = lost_foo_report(b"# moved\n", {OTHER: b"function Get-Foo { 1 }\n"})
    assert within["dependency_check"]["missing"] == [] and within["overall"] == L.REUSE_ALL


@pytest.mark.parametrize("new_lib", [
    b"<#\nhelp\n#>\nfunction Get-Foo { 1 }\n",                                 # real definition after a closed block comment
    b"$x = @'\ntext\n'@\nfunction Get-Foo { 1 }\n",                            # ... after a closed here-string
    b"function global:GET-FOO { 1 }\n",                                         # scope prefix and case
    b"function Get-Foo { 1 }\nfunction Get-Foo-Bar-Baz { 2 }\n",                # multi-hyphen neighbour
])
def test_legitimate_ascii_definitions_stay_present(new_lib):
    r = lost_foo_report(new_lib)
    assert r["dependency_check"]["missing"] == [] and r["overall"] == L.REUSE_ALL, r["dependency_check"]


def test_references_inside_comments_and_strings_stay_conservative():
    s = Store()
    base = ps_commit(s, {LIB: b"function Get-Foo { 1 }\n", USE: b"<#\nGet-Foo\n#>\n$t = @'\nGet-Foo\n'@\n"})
    head = ps_commit(s, {LIB: b"# gone\n", USE: b"<#\nGet-Foo\n#>\n$t = @'\nGet-Foo\n'@\n"}, base)
    r = dep_report(s, base, {LIB: head}, [head])
    assert r["dependency_check"]["missing"] == [{"caller": USE, "function": "Get-Foo"}]


def test_a_line_comment_opener_overstrips_only_toward_missing():
    # Disclosed approximation: "# <#" is a line comment in PowerShell, but the stripper treats it as a block opener,
    # so the real definition below reads as absent -> an extra missing entry (fail-closed), never a clean result.
    r = lost_foo_report(b"# <# not a block\nfunction Get-Foo { 1 }\n# #>\n")
    assert {"caller": USE, "function": "Get-Foo"} in r["dependency_check"]["missing"]


@pytest.mark.parametrize("new_lib", [
    b"function Get<# comment #>-Foo { 1 }\n",
    b"function Get@'\nnot a name\n'@-Foo { 1 }\n",
])
def test_stripped_spans_do_not_join_tokens_into_a_fake_definition(new_lib):
    # RCO1 independently found that replacing either span with "" fabricated Get-Foo.
    # PowerShell instead rejects the comment form or defines a different here-string name.
    r = lost_foo_report(new_lib)
    assert {"caller": USE, "function": "Get-Foo"} in r["dependency_check"]["missing"]
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED



# (id, leaf text, real PowerShell defines Get-Foo [5.1 and 7, parse only], the checker reports Get-Foo lost)
# Spans GLUED to a token are where the separator matters: a span after "function" or after the name must never leave
# a definition the parser rejects. keyword_space_comment_name is the disclosed fail-closed cost (reported lost).
GLUE_CASES = [
    ("keyword_glued", b"function<# c #>Get-Foo { 1 }\n", False, True),
    ("name_glued", b"function Get-Foo<# c #>{ 1 }\n", False, True),
    ("name_glued_herestring", b"function Get-Foo@'\nx\n'@{ 1 }\n", False, True),
    ("comment_then_def", b"<# c #>function Get-Foo { 1 }\n", True, False),
    ("def_space_comment_body", b"function Get-Foo <# c #>{ 1 }\n", True, False),
    ("keyword_space_comment_name", b"function <# c #>Get-Foo { 1 }\n", True, True),
    ("multiline_comment_then_def", b"<#\nnote\n#>\nfunction Get-Foo { 1 }\n", True, False),
    ("herestring_then_def", b"$x = @'\nabc\n'@\nfunction Get-Foo { 1 }\n", True, False),
    ("twin_split_name", b"function Get<# comment #>-Foo { 1 }\n", False, True),
    ("twin_herestring_name", b"function Get@'\nnot a name\n'@-Foo { 1 }\n", False, True),
]


@pytest.mark.parametrize("name, new_lib, ps_defines, lost", GLUE_CASES, ids=[c[0] for c in GLUE_CASES])
def test_spans_glued_to_a_token_never_leave_a_definition_powershell_rejects(name, new_lib, ps_defines, lost):
    assert ps_defines or lost, "a text PowerShell does not define must always be reported lost"
    r = lost_foo_report(new_lib)
    assert ({"caller": USE, "function": "Get-Foo"} in r["dependency_check"]["missing"]) is lost, r["dependency_check"]


_PS_SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))


@pytest.mark.skipif(not _PS_SHELLS, reason="the parser control needs a PowerShell host")
@pytest.mark.parametrize("shell", _PS_SHELLS, ids=lambda value: Path(value).stem)
def test_glue_case_table_matches_the_real_powershell_parser(shell, tmp_path):
    fixture = tmp_path / "glue_cases.json"
    fixture.write_text(json.dumps([c[1].decode("ascii") for c in GLUE_CASES]), encoding="utf-8")
    script = ("$texts = Get-Content -LiteralPath '%s' -Raw -Encoding UTF8 | ConvertFrom-Json; "
              "$out = foreach ($t in $texts) { $tok = $null; $err = $null; "
              "$ast = [System.Management.Automation.Language.Parser]::ParseInput([string]$t, [ref]$tok, [ref]$err); "
              "[bool](@($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)"
              " | ForEach-Object { $_.Name }) -ccontains 'Get-Foo') }; ConvertTo-Json -Compress -InputObject @($out)") % fixture
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [c[2] for c in GLUE_CASES]

def test_limits_disclose_the_definition_stripping_approximation():
    text = " ".join(L.LIMITS)
    for phrase in ("over-strip", "not a parser", "ASCII", "fail-closed", "never proves completeness"):
        assert phrase in text, phrase


# ---------------------------------------------------------------- committed-tree no-consumer tripwire
# A TRIPWIRE, not an isolation proof: a name assembled at runtime (string concatenation, a computed importlib name)
# is invisible; a test below pins that limit so it stays disclosed.
TRIPWIRE_ALLOWED = {"tools/bridge_leaf_reuse.py", "tests/tools/test_bridge_leaf_reuse.py"}
TRIPWIRE_PATTERN = r"bridge_leaf_reuse|leaf_reuse"
TRIPWIRE_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
TRIPWIRE_ENV.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def committed_consumers(repo, rev):
    grep = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "grep", "-I", "-l", "-i", "-E", TRIPWIRE_PATTERN,
                           rev, "--"], capture_output=True, text=True, env=TRIPWIRE_ENV)
    if grep.returncode not in (0, 1):
        raise RuntimeError("git grep failed: %s" % grep.stderr.strip())     # fail closed, never "no consumer"
    names = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "ls-tree", "-r", "--name-only", rev],
                           capture_output=True, text=True, env=TRIPWIRE_ENV)
    if names.returncode != 0:
        raise RuntimeError("git ls-tree failed: %s" % names.stderr.strip())
    paths = [line.split(":", 1)[1] for line in grep.stdout.splitlines() if line]
    paths += [p for p in names.stdout.splitlines() if re.search(TRIPWIRE_PATTERN, p, re.I)]   # same-named copies
    # Markdown mentions are documentation, not consumers.
    return sorted(p for p in dict.fromkeys(paths) if p not in TRIPWIRE_ALLOWED and not p.lower().endswith(".md"))


def tripwire_repo(root, files):
    root.mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=TRIPWIRE_ENV)
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, env=TRIPWIRE_ENV)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "c"], check=True, env=TRIPWIRE_ENV)
    return root


TRIPWIRE_BASE = {"tools/bridge_leaf_reuse.py": "x = 1\n", "tests/tools/test_bridge_leaf_reuse.py": "from tools import bridge_leaf_reuse\n",
                 "docs/NOTE.md": "see tools/bridge_leaf_reuse.py\n"}


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs the repository's own Git metadata")
def test_this_commit_has_no_committed_consumer_of_the_tool():
    assert committed_consumers(ROOT, "HEAD") == []


def test_tripwire_clean_synthetic_commit_has_no_consumer(tmp_path):
    assert committed_consumers(tripwire_repo(tmp_path / "r", TRIPWIRE_BASE), "HEAD") == []


@pytest.mark.parametrize("rel, body", [
    (".github/workflows/x.yml", "run: python tools/bridge_leaf_reuse.py --repo .\n"),
    ("start.py", "from tools import bridge_leaf_reuse\n"),
    ("core/gate.py", "import tools.Bridge_Leaf_Reuse\n"),
    (".agent-bridge/bin/bridge_leaf_reuse.py", "# same-named copy\n"),
    ("scripts/run.ps1", "python -m tools.bridge_leaf_reuse\n"),
    ("configs/x.yaml", "advisor: tools/bridge_leaf_reuse.py\n"),
])
def test_tripwire_sees_planted_committed_consumers(tmp_path, rel, body):
    assert committed_consumers(tripwire_repo(tmp_path / "r", dict(TRIPWIRE_BASE, **{rel: body})), "HEAD") == [rel]


def test_tripwire_audits_the_commit_not_uncommitted_files(tmp_path):
    repo = tripwire_repo(tmp_path / "r", TRIPWIRE_BASE)
    (repo / "start.py").write_text("from tools import bridge_leaf_reuse\n", encoding="utf-8")
    assert committed_consumers(repo, "HEAD") == []


@pytest.mark.parametrize("body", ["import importlib; importlib.import_module('tools.bridge_' + 'leaf' + '_re' + 'use')\n",
                                  "Set-Alias Get-Advice tools\\bridge_leaf_reuse_copy.py\n"])
def test_tripwire_disclosed_limit_runtime_assembled_names_are_invisible(tmp_path, body):
    hits = committed_consumers(tripwire_repo(tmp_path / "r", dict(TRIPWIRE_BASE, **{"core/dyn.py": body})), "HEAD")
    assert hits == ([] if "import_module" in body else ["core/dyn.py"])


def test_tripwire_unknown_revision_fails_closed(tmp_path):
    with pytest.raises(RuntimeError):
        committed_consumers(tripwire_repo(tmp_path / "r", TRIPWIRE_BASE), "0" * 40)


# ---------------------------------------------------------------- reader transport (deterministic fake cat-file)
FAKE = r'''
import sys, time
scenario = sys.argv[1]
OBJ = {"a" * 40: (b"blob", b"hello"), "b" * 40: (b"blob", b""), "c" * 40: (b"tree", b"")}
out = sys.stdout.buffer
def send(data):
    out.write(data)
    out.flush()
if scenario == "exit_early":
    sys.exit(3)
for line in sys.stdin.buffer:
    oid = line.strip()
    if scenario == "ok_then_fail" and oid == b"f" * 40:
        send(oid + b" blob 5\nhelloX")  # a real failed exchange after earlier successful (cached) reads
        continue
    if scenario in ("ok", "ignore_close", "ok_then_fail"):
        kind, data = OBJ.get(oid.decode(), (None, None))
        if kind is None:
            send(oid + b" missing\n")
        else:
            send(oid + b" " + kind + b" " + str(len(data)).encode() + b"\n" + data + b"\n")
        continue
    if scenario == "truncated_header":
        send(b"aaaa")
        sys.exit(0)
    if scenario == "wrong_oid":
        send(b"d" * 40 + b" blob 5\nhello\n")
    elif scenario == "wrong_type":
        send(oid + b" weird 5\nhello\n")
    elif scenario == "bad_size":
        send(oid + b" blob 5x\nhello\n")
    elif scenario == "negative_size":
        send(oid + b" blob -5\nhello\n")
    elif scenario == "huge_size":
        send(oid + b" blob 99999999999999999999\n")
    elif scenario == "over_limit":
        send(oid + b" blob 2048\n" + b"x" * 2048 + b"\n")
    elif scenario == "long_header":
        send(b"x" * 100000)
    elif scenario == "no_newline":
        send(oid + b" blob 5")
        sys.exit(0)
    elif scenario == "short_body":
        send(oid + b" blob 10\nabc")
        sys.exit(0)
    elif scenario == "bad_trailer":
        send(oid + b" blob 5\nhelloX")
    elif scenario == "missing_trailer_exit":
        send(oid + b" blob 5\nhello")
        sys.exit(0)
    elif scenario == "stall_mid_body":
        send(oid + b" blob 5\nhe")
    elif scenario != "stall":
        raise SystemExit("unknown scenario")
    time.sleep(120)
if scenario == "ignore_close":
    time.sleep(120)
'''


@pytest.fixture
def fake_git(tmp_path, monkeypatch):
    """Route the reader's Popen to a fake cat-file process; returns factory(scenario, **reader_kwargs)."""
    script = tmp_path / "fake_cat_file.py"
    script.write_text(FAKE, encoding="utf-8")
    real_popen = subprocess.Popen
    spawned, readers = [], []

    def factory(scenario, **kwargs):
        def popen(argv, **kw):
            assert argv[1:] == ["--no-replace-objects", "-C", "repo", "cat-file", "--batch"]
            proc = real_popen([sys.executable, str(script), scenario], **kw)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(L.subprocess, "Popen", popen)
        reader = L.GitObjectReader("repo", **kwargs)
        readers.append(reader)
        return reader

    factory.spawned = spawned
    yield factory
    for reader in readers:
        reader.close()
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)


def test_transport_valid_objects_empty_blob_missing_and_cache(fake_git):
    r = fake_git("ok", read_timeout=10)
    assert r.read("a" * 40) == ("blob", b"hello")
    assert r.read("b" * 40) == ("blob", b"")
    assert r.read("c" * 40) == ("tree", b"")
    assert r.read("e" * 40) is None and r.read("e" * 40) is None
    assert len(fake_git.spawned) == 1
    r.close()
    assert fake_git.spawned[0].poll() is not None and r.cleanup_errors == []


@pytest.mark.parametrize("scenario", [
    "truncated_header", "wrong_oid", "wrong_type", "bad_size", "negative_size", "huge_size", "over_limit",
    "long_header", "no_newline", "short_body", "bad_trailer", "missing_trailer_exit", "exit_early",
])
def test_transport_protocol_failures_raise_reader_error_and_never_cache(fake_git, scenario):
    r = fake_git(scenario, read_timeout=5, max_object_bytes=1024)
    started = time.monotonic()
    with pytest.raises(L.ReaderError):
        r.read("a" * 40)
    assert time.monotonic() - started < 5
    assert "a" * 40 not in r._cache
    assert fake_git.spawned[0].wait(5) is not None  # the failure itself ended the process, not close()
    with pytest.raises(L.ReaderError, match="broken"):  # a desynchronised stream is never reused
        r.read("a" * 40)
    assert len(fake_git.spawned) == 1
    r.close()
    assert fake_git.spawned[0].poll() is not None


@pytest.mark.parametrize("scenario", ["stall", "stall_mid_body"])
def test_transport_stall_times_out_and_kills_the_process(fake_git, scenario):
    r = fake_git(scenario, read_timeout=1.5)
    started = time.monotonic()
    with pytest.raises(L.ReaderError, match="timed out"):
        r.read("a" * 40)
    assert time.monotonic() - started < 5
    assert fake_git.spawned[0].wait(5) is not None  # the timeout itself killed the stalled process
    with pytest.raises(L.ReaderError, match="broken"):
        r.read("b" * 40)
    r.close()
    assert fake_git.spawned[0].poll() is not None and r.cleanup_errors == []


def test_transport_write_failure_after_process_exit(fake_git):
    r = fake_git("exit_early", read_timeout=5)
    r._start()
    fake_git.spawned[0].wait(10)
    with pytest.raises(L.ReaderError):
        r.read("a" * 40)


def test_transport_close_is_bounded_when_the_child_ignores_stdin_eof(fake_git):
    r = fake_git("ignore_close", read_timeout=5, close_timeout=1)
    assert r.read("a" * 40) == ("blob", b"hello")
    started = time.monotonic()
    r.close()
    assert time.monotonic() - started < 6
    assert fake_git.spawned[0].poll() is not None
    assert r.cleanup_errors == []  # a kill after the grace period is normal, bounded cleanup


def test_transport_close_failure_is_disclosed_not_swallowed(fake_git, monkeypatch):
    r = fake_git("ignore_close", read_timeout=5, close_timeout=0.5)
    assert r.read("a" * 40) == ("blob", b"hello")
    proc = fake_git.spawned[0]
    real_kill = proc.kill

    def broken_kill():
        raise OSError("injected kill failure")

    proc.kill = broken_kill
    try:
        r.close()
    finally:
        proc.kill = real_kill
    text = " | ".join(r.cleanup_errors)
    assert "injected kill failure" in text and "did not exit" in text


def test_assess_repo_reports_cleanup_failures_as_delta(monkeypatch):
    class Reader:
        def __init__(self, repo):
            self.cleanup_errors = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.cleanup_errors.append("injected cleanup failure")

        def read(self, oid):
            return None

    monkeypatch.setattr(L, "GitObjectReader", Reader)
    r = L.assess_repo("repo", "1" * 40, {"f.py": "2" * 40}, [])
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED
    assert r["reader_cleanup_errors"] == ["injected cleanup failure"]


def test_invalid_inputs_spawn_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: calls.append(a))
    reader = L.GitObjectReader("repo")
    for bad in ("HEAD", "c0ffee", "--all", "A" * 40):
        with pytest.raises(L.LeafReuseError):
            reader.read(bad)
    r = L.assess(reader, "HEAD", {"f.py": "1" * 40}, ["2" * 40])
    assert r["overall"] == L.DELTA_REVIEW_REQUIRED and calls == []


def test_reader_rejects_unbounded_configuration():
    for kwargs in ({"read_timeout": 0}, {"read_timeout": float("inf")}, {"close_timeout": -1},
                   {"max_object_bytes": 0}, {"read_timeout": "5"}, {"max_object_bytes": True}):
        with pytest.raises(L.LeafReuseError):
            L.GitObjectReader("repo", **kwargs)


def test_real_reader_reads_commit_tree_empty_blob_and_missing():
    with L.GitObjectReader(ROOT) as reader:
        if reader.read(R1276) is None:
            pytest.skip("reviewed objects absent from this clone")
        kind, data = reader.read(R1276)
        assert kind == "commit" and data.startswith(b"tree ")
        assert reader.read(data[5:45].decode())[0] == "tree"
        empty = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
        if reader.read(empty) is not None:
            assert reader.read(empty) == ("blob", b"")
        assert reader.read("0" * 40) is None
    assert reader.cleanup_errors == []


# ---------------------------------------------------------------- broken reader never serves its cache (RCO2 78d9dfb7)
def test_cached_objects_are_served_only_before_a_failed_exchange(fake_git):
    r = fake_git("ok_then_fail", read_timeout=5)
    assert r.read("a" * 40) == ("blob", b"hello")       # positive twins before the failure: cached existing...
    assert r.read("e" * 40) is None                     # ...and cached missing
    assert r.read("a" * 40) == ("blob", b"hello") and r.read("e" * 40) is None
    with pytest.raises(L.ReaderError, match="trailer"):
        r.read("f" * 40)                                # the real failed exchange (bad trailer) breaks the reader
    for oid in ("a" * 40, "e" * 40, "c" * 40):          # cached existing, cached missing, uncached: all refused
        with pytest.raises(L.ReaderError, match="broken"):
            r.read(oid)
    assert len(fake_git.spawned) == 1


def test_invalid_ids_are_still_rejected_first_on_a_broken_reader(fake_git):
    r = fake_git("ok_then_fail", read_timeout=5)
    with pytest.raises(L.ReaderError):
        r.read("f" * 40)
    for bad in ("HEAD", "A" * 40, "c0ffee"):
        with pytest.raises(L.LeafReuseError) as caught:
            r.read(bad)
        assert not isinstance(caught.value, L.ReaderError) or "broken" not in str(caught.value)
