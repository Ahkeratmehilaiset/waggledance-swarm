"""Synthetic tests for the default-off repository document content projection (increment 2)."""
import hashlib
import json
import os
import subprocess

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_doc_projection as dp

A = chr(0xE4)
O = chr(0xF6)
LF = bytes([10])
CRLF = bytes([13, 10])
FINNISH = "T" + A + "m" + A + " dokumentti kertoo miten parvi k" + A + "ytt" + O + A + " muistia."
SECOND = "Toinen kappale: lane raportoi tilan ja seuraavan askeleen."
WHEN = "2026-11-01T10:00:00+00:00"
FIXTURE_MAIL = "fixture" + chr(64) + "example.invalid"


def git_env():
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "fixture",
                "GIT_AUTHOR_EMAIL": FIXTURE_MAIL, "GIT_COMMITTER_NAME": "fixture",
                "GIT_COMMITTER_EMAIL": FIXTURE_MAIL, "GIT_AUTHOR_DATE": WHEN, "GIT_COMMITTER_DATE": WHEN,
                "GIT_TERMINAL_PROMPT": "0"})
    return env


def git(root, *args, data=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, env=git_env(),
                            check=True)
    return result.stdout


def make_repo(tmp_path, files, links=(), modules=(), executables=()):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        git(root, "add", "--", path)
    for path, text in links:
        oid = git(root, "hash-object", "-w", "--stdin", data=text).decode("ascii").strip()
        git(root, "update-index", "--add", "--cacheinfo", "120000," + oid + "," + path)
    for path in modules:
        git(root, "update-index", "--add", "--cacheinfo", "160000," + "ab" * 20 + "," + path)
    for path in executables:
        git(root, "update-index", "--chmod=+x", "--", path)
    git(root, "commit", "-q", "-m", "fixture")
    return root, git(root, "rev-parse", "HEAD").decode("ascii").strip()


def blob_of(root, commit, path):
    return git(root, "rev-parse", commit + ":" + path).decode("ascii").strip()


def policy(commit, paths, removed=(), section=4096, chunk=300, max_bytes=262144):
    doc = {"schema": dp.POLICY_SCHEMA, "commit": commit, "paths": list(paths), "removed_blobs": list(removed),
           "max_section_chars": section, "max_chunk_chars": chunk, "max_doc_bytes": max_bytes}
    raw = json.dumps(doc).encode("ascii")
    return raw, hashlib.sha256(raw).hexdigest()


def build(root, raw, sha):
    return dp.build_doc_projection(enabled=True, repo_root=str(root), policy_bytes=raw, policy_sha256=sha)


def texts(projection):
    return [cp.decode_text(record["stored_text"]) for record in projection["records"]]


def doc_bytes(*paragraphs):
    return ("# Otsikko" + chr(10) + chr(10) + (chr(10) + chr(10)).join(paragraphs) + chr(10)).encode("utf-8")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path, {"docs/guide.md": doc_bytes(FINNISH, SECOND), "docs/other.md": doc_bytes(SECOND)})


# --- default off, enable flag ---------------------------------------------------------------------------------------

def test_default_is_off_and_reads_nothing():
    assert dp.build_doc_projection() == {"status": "OFF"}
    assert dp.build_doc_projection(enabled=False, repo_root="no-such-dir") == {"status": "OFF"}


@pytest.mark.parametrize("value", [1, "true", None, 0])
def test_enable_flag_must_be_exactly_true(value):
    with pytest.raises(dp.DocRefused, match="enabled_invalid"):
        dp.build_doc_projection(enabled=value)


# --- happy path, Unicode and provenance -----------------------------------------------------------------------------

def test_finnish_document_round_trips_with_provenance(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/guide.md"])
    projection = build(root, raw, sha)
    assert texts(projection) == ["# Otsikko", FINNISH, SECOND]
    assert projection["accepted"] == 3 and projection["refused_docs"] == {} and projection["refused_sections"] == {}
    assert projection["schema"] == dp.SCHEMA and projection["commit"] == commit and projection["policy_sha256"] == sha
    assert projection["authority"] == "none" and projection["text_encoding"] == cp.TEXT_ENCODING
    blob = blob_of(root, commit, "docs/guide.md")
    source = hashlib.sha256(cp._canonical({"blob": blob, "commit": commit, "path": "docs/guide.md"})).hexdigest()
    for index, record in enumerate(projection["records"]):
        prov = record["provenance"]
        assert prov["source_kind"] == "repo_doc" and prov["commit"] == commit and prov["path"] == "docs/guide.md"
        assert prov["blob_id"] == blob and prov["source_doc_ref"] == source and prov["section_index"] == index
        assert prov["chunk_index"] == 0 and prov["chunk_count"] == 1 and prov["canonical_event"] is False
        assert record["time_utc"] == "2026-11-01T10:00:00Z" and record["kind"] == "document"
        assert record["record_id"] == "doc:" + prov["event_ref"]
        assert cp._OUTPUT.fullmatch(record["stored_text"])
    assert A in "".join(texts(projection))


def test_only_allowlisted_paths_are_read(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/other.md"])
    projection = build(root, raw, sha)
    assert {r["provenance"]["path"] for r in projection["records"]} == {"docs/other.md"}


def test_documents_are_read_at_the_pinned_commit_not_the_worktree(repo):
    root, commit = repo
    (root / "docs" / "guide.md").write_bytes(doc_bytes("Muokattu tyopuun teksti joka ei ole commitissa."))
    raw, sha = policy(commit, ["docs/guide.md"])
    assert texts(build(root, raw, sha)) == ["# Otsikko", FINNISH, SECOND]


def test_replace_objects_cannot_swap_the_pinned_content(repo):
    root, commit = repo
    old = blob_of(root, commit, "docs/guide.md")
    new = git(root, "hash-object", "-w", "--stdin", data=doc_bytes("Vaihdettu sisalto.")).decode("ascii").strip()
    git(root, "replace", old, new)
    assert git(root, "cat-file", "blob", old) == doc_bytes("Vaihdettu sisalto.")
    raw, sha = policy(commit, ["docs/guide.md"])
    assert texts(build(root, raw, sha)) == ["# Otsikko", FINNISH, SECOND]


def test_long_section_splits_on_lines_and_a_long_line_drops(tmp_path):
    lines = ["rivi numero %d kertoo jotain hyodyllista" % i for i in range(40)]
    data = (chr(10).join(lines) + chr(10) + chr(10) + "x" * 300).encode("ascii")
    root, commit = make_repo(tmp_path, {"docs/a.md": data})
    raw, sha = policy(commit, ["docs/a.md"], section=200)
    projection = build(root, raw, sha)
    kept = texts(projection)
    assert len(kept) > 1 and all(len(text) <= 200 for text in kept)
    assert " ".join(kept) == " ".join(lines)
    assert projection["refused_sections"] == {"oversize": 1}


def test_chunks_respect_the_policy_chunk_limit(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes((FINNISH + " ") * 10)})
    raw, sha = policy(commit, ["docs/a.md"], chunk=64)
    projection = build(root, raw, sha)
    records = [r for r in projection["records"] if r["provenance"]["section_index"] == 1]
    assert len(records) > 1 and all(len(r["stored_text"]) <= 64 for r in records)
    assert {r["provenance"]["chunk_count"] for r in records} == {len(records)}
    assert [r["provenance"]["chunk_index"] for r in records] == list(range(len(records)))
    joined = "".join(cp.decode_text(r["stored_text"]) for r in records)
    assert joined.replace(" ", "") == (FINNISH * 10).replace(" ", "")


# --- per-section and whole-document screening -----------------------------------------------------------------------

def test_personal_section_drops_alone(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes(FINNISH, "Yhteys " + FIXTURE_MAIL, SECOND)})
    raw, sha = policy(commit, ["docs/a.md"])
    projection = build(root, raw, sha)
    assert texts(projection) == ["# Otsikko", FINNISH, SECOND]
    assert projection["refused_sections"] == {"personal": 1}


@pytest.mark.parametrize("bad, token", [
    ("api_key = " + "Zq8" * 8, "credential"),
    ("-----BEGIN PRIVATE " + "KEY-----", "credential"),
    ("the " + "gold" + " answers live here", "sealed"),
    ("a " + "heldout" + " split", "sealed"),
])
def test_credential_or_sealed_anywhere_drops_the_whole_document(tmp_path, bad, token):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes(FINNISH, bad, SECOND)})
    raw, sha = policy(commit, ["docs/a.md"])
    projection = build(root, raw, sha)
    assert projection["records"] == [] and projection["refused_docs"] == {token: 1}


def test_marker_split_across_lines_still_drops_the_document(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes(FINNISH, "api_key =" + chr(10) + "Zq8" * 8)})
    raw, sha = policy(commit, ["docs/a.md"])
    assert build(root, raw, sha)["refused_docs"] == {"credential": 1}


# --- object kinds and content checks --------------------------------------------------------------------------------

def test_symlink_submodule_executable_and_missing_paths_are_counted(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes(FINNISH), "docs/run.md": doc_bytes(SECOND)},
                             links=[("docs/link.md", b"docs/a.md")], modules=["docs/sub.md"],
                             executables=["docs/run.md"])
    paths = ["docs/a.md", "docs/link.md", "docs/sub.md", "docs/run.md", "docs/missing.md", "docs/a.md.md"]
    raw, sha = policy(commit, paths)
    projection = build(root, raw, sha)
    assert {r["provenance"]["path"] for r in projection["records"]} == {"docs/a.md"}
    assert projection["refused_docs"] == {"symlink": 1, "submodule": 1, "not_regular": 1, "path_missing": 2}


def test_directory_entry_is_not_a_blob(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/guide.md/inner.txt": doc_bytes(SECOND)})
    raw, sha = policy(commit, ["docs/guide.md"])
    assert build(root, raw, sha)["refused_docs"] == {"not_blob": 1}


@pytest.mark.parametrize("data, token", [
    (b"text" + bytes([0]) + b"more", "binary"),
    (b"bad " + bytes([0xFF, 0xFE]) + b" utf8", "encoding"),
    (b"x" * 2000, "oversize"),
])
def test_binary_invalid_and_oversize_documents_are_counted(tmp_path, data, token):
    root, commit = make_repo(tmp_path, {"docs/a.md": data})
    raw, sha = policy(commit, ["docs/a.md"], max_bytes=1000)
    projection = build(root, raw, sha)
    assert projection["records"] == [] and projection["refused_docs"] == {token: 1}


def test_crlf_document_matches_lf_document(tmp_path):
    lf = doc_bytes(FINNISH, SECOND)
    root, commit = make_repo(tmp_path, {"docs/a.md": lf, "docs/b.md": lf.replace(LF, CRLF)})
    assert blob_of(root, commit, "docs/a.md") != blob_of(root, commit, "docs/b.md")
    raw, sha = policy(commit, ["docs/a.md", "docs/b.md"])
    projection = build(root, raw, sha)
    by_path = {}
    for record in projection["records"]:
        by_path.setdefault(record["provenance"]["path"], []).append(record["stored_text"])
    assert by_path["docs/a.md"] == by_path["docs/b.md"] and len(by_path["docs/a.md"]) == 3


# --- removal and validation -----------------------------------------------------------------------------------------

def test_removed_blob_is_not_projected_and_validator_refuses_a_survivor(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/guide.md", "docs/other.md"])
    before = build(root, raw, sha)
    blob = blob_of(root, commit, "docs/guide.md")
    raw2, sha2 = policy(commit, ["docs/guide.md", "docs/other.md"], removed=[blob])
    after = build(root, raw2, sha2)
    assert after["refused_docs"] == {"removed": 1}
    assert {r["provenance"]["path"] for r in after["records"]} == {"docs/other.md"}
    assert dp.validate_doc_projection(after, repo_root=str(root), policy_bytes=raw2, policy_sha256=sha2) is None
    stale = dict(before, policy_sha256=sha2)
    with pytest.raises(dp.DocRefused, match="projection_mismatch"):
        dp.validate_doc_projection(stale, repo_root=str(root), policy_bytes=raw2, policy_sha256=sha2)


def test_validator_refuses_any_tampering(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/guide.md"])
    projection = build(root, raw, sha)
    assert dp.validate_doc_projection(projection, repo_root=str(root), policy_bytes=raw, policy_sha256=sha) is None
    tampered = json.loads(json.dumps(projection))
    tampered["records"][1]["stored_text"] = cp.encode_text("vaihdettu")
    for bad in (tampered, dict(projection, extra=1), [], dict(projection, accepted=0)):
        with pytest.raises(dp.DocRefused, match="projection_mismatch"):
            dp.validate_doc_projection(bad, repo_root=str(root), policy_bytes=raw, policy_sha256=sha)


def test_projection_is_deterministic_and_path_order_free(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/other.md", "docs/guide.md"])
    assert build(root, raw, sha) == build(root, raw, sha)
    raw2, sha2 = policy(commit, ["docs/guide.md", "docs/other.md"])
    assert build(root, raw2, sha2)["records"] == build(root, raw, sha)["records"]


# --- policy and repository refusal ----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    ".codex-audit/a.md", "memory/a.md", "docs/memory/a.md", "tests/security/a.md", "docs/sealed_notes.md",
    "docs/gold.md", "docs/judge.md", "docs/HUMAN_APPROVAL.md", "docs/secrets.md", "docs/credentials.md",
    "docs/password.md", "docs/tokens.md", "docs/private.md", "docs/a.pem", "docs/a.key", ".env", "docs/.env.md",
    "docs/a.py", "docs/a.MD", "docs/../a.md", "/docs/a.md", "docs//a.md", "docs" + chr(92) + "a.md",
    "C:/docs/a.md", "docs/a b.md", "docs/*.md", ":(glob)docs/a.md", "", "docs/" + "a" * 300 + ".md",
    ".agent-bridge/a.md", ".git/a.md", "docs/a.md/", "docs/a.json", "docs/a.env.md", "docs/a.pem.md",
    "docs/memories/a.md", "docs/a" + chr(0xE4) + ".md",
])
def test_unsafe_or_unclear_paths_refuse_the_policy(repo, path):
    root, commit = repo
    raw, sha = policy(commit, [path])
    with pytest.raises(dp.DocRefused, match="policy_refused"):
        build(root, raw, sha)


@pytest.mark.parametrize("path", ["README.md", "docs/guide.md", "docs/arch/RUNBOOK_V1.md", "notes/a-b.c_d.txt",
                                  "docs/x.rst"])
def test_plain_document_paths_are_accepted(path):
    assert dp._doc_path(path) is True


@pytest.mark.parametrize("change", [
    {"schema": "wd.mind-doc-policy.v0"}, {"commit": "ab" * 19}, {"commit": "AB" * 20}, {"paths": []},
    {"paths": ["docs/a.md", "docs/a.md"]}, {"removed_blobs": ["x"]}, {"max_section_chars": 0},
    {"max_section_chars": 4097}, {"max_chunk_chars": 15}, {"max_chunk_chars": 301}, {"max_doc_bytes": 0},
    {"max_doc_bytes": 262145}, {"extra": 1}, {"max_doc_bytes": True}, {"paths": "docs/a.md"},
])
def test_policy_fields_are_closed(repo, change):
    root, commit = repo
    doc = {"schema": dp.POLICY_SCHEMA, "commit": commit, "paths": ["docs/guide.md"], "removed_blobs": [],
           "max_section_chars": 4096, "max_chunk_chars": 300, "max_doc_bytes": 262144}
    doc.update(change)
    raw = json.dumps(doc).encode("ascii")
    with pytest.raises(dp.DocRefused, match="policy_refused"):
        build(root, raw, hashlib.sha256(raw).hexdigest())


def test_policy_hash_must_match_and_duplicate_keys_refuse(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="policy_refused"):
        build(root, raw, "0" * 64)
    dup = raw[:-1] + b', "paths": ["docs/other.md"]}'
    with pytest.raises(dp.DocRefused, match="policy_refused"):
        build(root, dup, hashlib.sha256(dup).hexdigest())


def test_unknown_commit_and_non_repository_refuse(repo, tmp_path):
    root, commit = repo
    raw, sha = policy("cd" * 20, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="commit_refused"):
        build(root, raw, sha)
    blob = blob_of(root, commit, "docs/guide.md")
    raw, sha = policy(blob, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="commit_refused"):
        build(root, raw, sha)
    plain = tmp_path / "plain"
    plain.mkdir()
    raw, sha = policy(commit, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="repo_refused"):
        build(plain, raw, sha)
    for bad in (None, 5, "", b"x"):
        with pytest.raises(dp.DocRefused, match="repo_refused"):
            dp.build_doc_projection(enabled=True, repo_root=bad, policy_bytes=raw, policy_sha256=sha)


def test_refusals_never_carry_input_text(tmp_path):
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes("api_key = " + "Zq8" * 8)})
    raw, sha = policy(commit, ["docs/a.md"])
    assert "Zq8" not in json.dumps(build(root, raw, sha))
    with pytest.raises(dp.DocRefused) as caught:
        build(root, raw, "f" * 64)
    assert str(caught.value) == "policy_refused"


def test_hex_run_is_redacted_from_its_section(tmp_path):
    value = "a1" * 20
    root, commit = make_repo(tmp_path, {"docs/a.md": doc_bytes(FINNISH, "commit " + value + " landed", SECOND)})
    raw, sha = policy(commit, ["docs/a.md"])
    projection = build(root, raw, sha)
    assert texts(projection) == ["# Otsikko", FINNISH, "commit hex-redacted landed", SECOND]
    assert value not in json.dumps(projection)
    assert projection["refused_sections"] == {} and projection["refused_docs"] == {}


def test_inherited_git_variables_cannot_redirect_the_read(repo, tmp_path, monkeypatch):
    root, commit = repo
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(other / ".git" / "objects"))
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "")
    raw, sha = policy(commit, ["docs/guide.md"])
    assert texts(build(root, raw, sha)) == ["# Otsikko", FINNISH, SECOND]


def test_a_subdirectory_of_a_repository_is_not_a_repository_root(repo):
    root, commit = repo
    raw, sha = policy(commit, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="repo_refused"):
        build(root / "docs", raw, sha)


def test_byte_order_mark_and_trailing_spaces_do_not_change_the_text(tmp_path):
    plain = doc_bytes(FINNISH, SECOND)
    marked = bytes([0xEF, 0xBB, 0xBF]) + plain.replace(b"askeleen.", b"askeleen.   ")
    root, commit = make_repo(tmp_path, {"docs/a.md": plain, "docs/b.md": marked})
    raw, sha = policy(commit, ["docs/a.md", "docs/b.md"])
    by_path = {}
    for record in build(root, raw, sha)["records"]:
        by_path.setdefault(record["provenance"]["path"], []).append(record["stored_text"])
    assert by_path["docs/a.md"] == by_path["docs/b.md"]


def test_document_byte_limit_counts_bytes_not_characters(tmp_path):
    data = (A * 600).encode("utf-8")
    root, commit = make_repo(tmp_path, {"docs/a.md": data})
    raw, sha = policy(commit, ["docs/a.md"], max_bytes=1000)
    assert len(data) == 1200 and build(root, raw, sha)["refused_docs"] == {"oversize": 1}


def test_an_annotated_tag_id_is_not_a_commit_pin(repo):
    root, commit = repo
    git(root, "tag", "-a", "-m", "fixture tag", "v1", commit)
    tag = git(root, "rev-parse", "v1").decode("ascii").strip()
    assert tag != commit
    raw, sha = policy(tag, ["docs/guide.md"])
    with pytest.raises(dp.DocRefused, match="commit_refused"):
        build(root, raw, sha)


def test_tree_listing_entries_never_stand_in_for_the_named_path(repo):
    root, commit = repo
    env = dp._git_env(str(root))
    assert dp._entry(str(root), env, commit, "docs/") == (None, "path_missing")
    assert dp._entry(str(root), env, commit, "docs") == (None, "not_blob")
    assert dp._entry(str(root), env, commit, "docs/guide.md")[1] is None
