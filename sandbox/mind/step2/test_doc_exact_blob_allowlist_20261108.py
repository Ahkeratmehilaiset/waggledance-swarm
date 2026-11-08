"""Synthetic exact document admission; never read real documents or build a memory index."""
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_doc_projection as doc
from sandbox.mind.step2 import test_shared_doc_projection as fx


@pytest.fixture
def synthetic(tmp_path):
    files = {
        "docs/allowed.md": fx.doc_bytes("Synthetic build work continues."),
        "docs/quote.md": fx.doc_bytes("RCO2 said: the change is safe."),
        "docs/paraphrase.md": fx.doc_bytes("The second checker says the change is good."),
    }
    root, commit = fx.make_repo(tmp_path, files)
    blobs = {path: fx.blob_of(root, commit, path) for path in files}
    return root, commit, blobs


def policy(commit, paths, approved, **changes):
    base, _ = fx.policy(commit, paths)
    value = json.loads(base)
    value.update(schema="wd.mind-doc-policy.v2", approved_doc_refs=approved)
    value.update(changes)
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    return data, hashlib.sha256(data).hexdigest()


def ref(commit, path, blob):
    return {"commit": commit, "path": path, "blob": blob}


def test_legacy_screen_does_not_detect_review_paraphrases(synthetic):
    root, commit, blobs = synthetic
    data, pin = fx.policy(commit, ["docs/quote.md", "docs/paraphrase.md"])
    projection = fx.build(root, data, pin)
    text = " ".join(fx.texts(projection))
    assert "RCO2 said" in text and "second checker" in text


def test_only_reviewed_exact_commit_path_blob_can_enter(synthetic):
    root, commit, blobs = synthetic
    approved = [ref(commit, "docs/allowed.md", blobs["docs/allowed.md"])]
    data, pin = policy(commit, list(blobs), approved)
    projection = fx.build(root, data, pin)
    assert {item["provenance"]["path"] for item in projection["records"]} == {"docs/allowed.md"}
    assert all(item["provenance"]["blob_id"] == blobs["docs/allowed.md"] for item in projection["records"])
    doc.validate_doc_projection(projection, repo_root=str(root), policy_bytes=data, policy_sha256=pin, **fx.git_pin())
    assert projection["refused_docs"] == {}  # unapproved sources are silently excluded
    changed, changed_pin = policy(commit, list(blobs), approved + [ref(commit, "docs/quote.md", blobs["docs/quote.md"])])
    with pytest.raises(doc.DocRefused):
        doc.validate_doc_projection(projection, repo_root=str(root), policy_bytes=changed,
                                    policy_sha256=changed_pin, **fx.git_pin())


def test_empty_allowlist_reads_no_document_paths(synthetic, monkeypatch):
    root, commit, blobs = synthetic
    data, pin = policy(commit, list(blobs), [])
    monkeypatch.setattr(doc, "_entry", lambda *a: pytest.fail("unapproved document path was read"))
    assert fx.build(root, data, pin)["records"] == []


def test_wrong_blob_refuses_before_blob_bytes_are_read(synthetic, monkeypatch):
    root, commit, blobs = synthetic
    data, pin = policy(commit, list(blobs), [ref(commit, "docs/allowed.md", "0" * 40)])
    monkeypatch.setattr(doc, "_read_blob", lambda *a: pytest.fail("unapproved blob was read"))
    with pytest.raises(doc.DocRefused):
        fx.build(root, data, pin)


@pytest.mark.parametrize("case", ["wrong_commit", "wrong_path", "uppercase_blob", "duplicate", "unknown_key", "not_list"])
def test_bad_approval_tuples_fail_closed(synthetic, case):
    _, commit, blobs = synthetic
    good = ref(commit, "docs/allowed.md", blobs["docs/allowed.md"])
    if case == "wrong_commit":
        approved = [dict(good, commit="0" * 40)]
    elif case == "wrong_path":
        approved = [dict(good, path="docs/not-listed.md")]
    elif case == "uppercase_blob":
        approved = [dict(good, blob="A" * 40)]
    elif case == "duplicate":
        approved = [good, good]
    elif case == "unknown_key":
        approved = [dict(good, extra="x")]
    else:
        approved = None
    data, pin = policy(commit, list(blobs), approved)
    with pytest.raises(doc.DocRefused, match="^policy_refused$"):
        doc.load_doc_policy(data, pin)


def test_removed_blob_and_policy_hash_are_still_enforced(synthetic):
    root, commit, blobs = synthetic
    path = "docs/allowed.md"
    data, pin = policy(commit, list(blobs), [ref(commit, path, blobs[path])], removed_blobs=[blobs[path]])
    projection = fx.build(root, data, pin)
    assert projection["records"] == [] and projection["refused_docs"]["removed"] == 1
    with pytest.raises(doc.DocRefused, match="^policy_refused$"):
        doc.load_doc_policy(data, "0" * 64)


def test_allowlisting_a_review_quote_is_not_semantic_approval(synthetic):
    root, commit, blobs = synthetic
    path = "docs/quote.md"
    data, pin = policy(commit, list(blobs), [ref(commit, path, blobs[path])])
    assert "RCO2 said" in " ".join(fx.texts(fx.build(root, data, pin)))


@pytest.mark.parametrize("kind", ["commit", "tree", "blob"])
def test_v2_modified_git_object_refuses(synthetic, monkeypatch, kind):
    root, commit, blobs = synthetic
    path = "docs/allowed.md"
    data, pin = policy(commit, list(blobs), [ref(commit, path, blobs[path])])
    original = doc._git
    def altered(root, env, *args):
        value = original(root, env, *args)
        if len(args) >= 2 and args[:2] == ("cat-file", kind):
            return bytes([value[0] ^ 1]) + value[1:]
        return value
    monkeypatch.setattr(doc, "_git", altered)
    with pytest.raises(doc.DocRefused):
        fx.build(root, data, pin)


def test_v2_missing_commit_refuses(synthetic):
    root, _, blobs = synthetic
    missing = "0" * 40
    path = "docs/allowed.md"
    data, pin = policy(missing, list(blobs), [ref(missing, path, blobs[path])])
    with pytest.raises(doc.DocRefused, match="^commit_refused$"):
        fx.build(root, data, pin)


def test_v2_does_not_follow_an_approved_git_symlink(tmp_path):
    path = "docs/synthetic-link.md"
    root, commit = fx.make_repo(tmp_path, {}, links=[(path, b"synthetic-target")])
    blob = fx.blob_of(root, commit, path)
    data, pin = policy(commit, [path], [ref(commit, path, blob)])
    projection = fx.build(root, data, pin)
    assert projection["records"] == [] and projection["refused_docs"]["symlink"] == 1
