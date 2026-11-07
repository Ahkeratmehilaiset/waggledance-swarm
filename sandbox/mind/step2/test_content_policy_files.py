"""The shipped content policy files (route B, Lead decision 2026-11-07 18:56Z) load under their pinned schemas and
stay inside the curated bounds: the message policy reuses the provisioned non-review task list, the document policy
is an exact architecture and runbook allowlist whose records stay under half of the builder's record bound."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_doc_projection as dp
from sandbox.mind.step2 import shared_index_builder as sib

ROOT = Path(__file__).resolve().parents[3]
MESSAGE = ROOT / "configs" / "mind" / "content_message_policy_v1.json"
DOCS = ROOT / "configs" / "mind" / "content_doc_policy_v1.json"
PILOT = ROOT / "configs" / "mind" / "pilot_non_review_policy.json"
DOC_RECORD_BUDGET = sib.MAX_RECORDS // 2


def pinned(path):
    raw = path.read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def test_message_policy_reuses_the_provisioned_non_review_task_list():
    raw, sha = pinned(MESSAGE)
    policy = cp.load_content_policy(raw, sha)
    assert policy["approved_tasks"] == frozenset(json.loads(PILOT.read_bytes())["approved_tasks"])
    assert policy["lanes"] == frozenset({"codex-lead-1", "codex-tools-1", "fable-5"})
    assert policy["types"] == frozenset({"message", "progress", "done", "milestone", "status", "handoff", "test"})
    assert policy["removed_source_refs"] == frozenset()
    assert policy["max_message_chars"] == cp.MAX_MESSAGE_CHARS and policy["max_chunk_chars"] == cp.MAX_CHUNK_CHARS


def test_document_policy_is_an_exact_architecture_and_runbook_allowlist():
    raw, sha = pinned(DOCS)
    policy = dp.load_doc_policy(raw, sha)
    assert 20 <= len(policy["paths"]) <= 64
    for path in policy["paths"]:
        lowered = path.lower()
        assert path.startswith("docs/architecture/") or "runbook" in lowered
        assert not path.startswith("docs/runs/") and "rco" not in lowered and "review" not in lowered
    assert policy["removed_blobs"] == frozenset()
    assert policy["max_doc_bytes"] == dp.MAX_DOC_BYTES and policy["max_section_chars"] == dp.MAX_SECTION_CHARS
    assert json.loads(raw)["paths"] == sorted(policy["paths"])


def test_both_policy_files_are_plain_ascii_json_with_sorted_keys():
    for path in (MESSAGE, DOCS):
        raw = path.read_bytes()
        doc = json.loads(raw.decode("ascii"))
        assert list(doc) == sorted(doc)


def _commit_present(commit):
    result = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", commit + "^{commit}"], capture_output=True)
    return result.returncode == 0


def test_document_policy_record_budget_at_its_pinned_commit():
    raw, sha = pinned(DOCS)
    commit = json.loads(raw)["commit"]
    if not _commit_present(commit):
        pytest.skip("the pinned document commit is not in this clone")
    projection = dp.build_doc_projection(enabled=True, repo_root=str(ROOT), policy_bytes=raw, policy_sha256=sha)
    assert projection["refused_docs"] == {}
    assert 0 < projection["accepted"] <= DOC_RECORD_BUDGET
    assert {r["provenance"]["path"] for r in projection["records"]} == set(json.loads(raw)["paths"])
