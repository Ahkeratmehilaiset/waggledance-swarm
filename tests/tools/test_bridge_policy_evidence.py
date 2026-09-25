# SPDX-License-Identifier: BUSL-1.1
"""Tests for the bounded read-only evidence emitter.

Most of this file is about refusal: traversal, aliases, reparse points, files
that move under the reader, and evidence classes with nothing behind them. The
last section joins the emitter to the epoch builder and to admission, because
the property that matters end to end is that a world we cannot evidence parks.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_capacity_advisor import InputError  # noqa: E402
from tools.bridge_policy_epochs import EPOCH_SNAPSHOT_SCHEMA  # noqa: E402
from tools.bridge_policy_evidence import (  # noqa: E402
    EVIDENCE_SOURCE_SCHEMA,
    FILE_BACKED_CLASSES,
    MAX_FILE_BYTES,
    allowlisted_root,
    build_snapshot,
    emit_evidence,
    inspect_sources,
)
from tools.bridge_task_admission import EPOCH_FIELDS, KEEP, PARK, admit  # noqa: E402

BINDING = {
    "agent_id": "fable-5",
    "session_id": "wd-fable-direct-20260924T090104Z",
    "native_thread_id": "9f375967-f824-4e2e-8104-7f0011117cf5",
    "task_id": "codex-lead-1/autonomy-evidence-source-20260925",
    "request_id": "7d20f6c8-8ecd-4409-84ee-ab5b2aed5ab7",
    "head": "f262691d2098fbd90a003fa33721753326f20ca9",
    "claim_id": "fable-5/autonomy-evidence-source-20260925",
    "scope_digest": "e1" * 32,
    "authority_ref": "operator/finish-implementation",
    "policy_digest": "e2" * 32,
    "permission_digest": "e3" * 32,
    "native_pid": 24400,
    "native_process_started_at": "2026-09-24T09:01:04.252477+00:00",
}


@pytest.fixture
def tree(tmp_path):
    """A small allowlisted root with one file behind each class."""
    (tmp_path / "configs" / "policy").mkdir(parents=True)
    (tmp_path / "configs" / "policy" / "constitution.yaml").write_text(
        "rule: never fabricate\n", encoding="utf-8")
    (tmp_path / "configs" / "policy" / "profiles.yaml").write_text(
        "profiles: [a, b]\n", encoding="utf-8")
    (tmp_path / "configs" / "catalog.json").write_text('{"profiles": []}', encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "qualification.json").write_text('{"runs": 3}', encoding="utf-8")
    (tmp_path / "configs" / "profile.json").write_text('{"id": "p"}', encoding="utf-8")
    return tmp_path


def manifest():
    return {
        "policy": {"documents": ["configs/policy/constitution.yaml",
                                 "configs/policy/profiles.yaml"]},
        "catalog": {"document": "configs/catalog.json"},
        "qualification": {"document": "reports/qualification.json",
                          "evidence_ids": ["ev-1", "ev-2"],
                          "verdicts": {"fable-producer/default": "qualified"}},
        "profile": {"document": "configs/profile.json",
                    "profile_id": "fable-producer/default",
                    "authorization_ref": "operator/2026-09-25"},
    }


# --- it hashes the real bytes -------------------------------------------------


def test_digests_are_of_the_actual_file_content(tree):
    emitted = emit_evidence(root=tree, manifest=manifest())
    expected = hashlib.sha256((tree / "configs" / "catalog.json").read_bytes()).hexdigest()
    assert emitted["evidence"]["catalog"]["sha256"] == expected
    assert emitted["schema"] == EVIDENCE_SOURCE_SCHEMA
    assert emitted["unavailable"] == {}
    assert emitted["documents_hashed"] == 5


def test_editing_a_source_changes_its_digest(tree):
    before = emit_evidence(root=tree, manifest=manifest())["evidence"]["catalog"]["sha256"]
    (tree / "configs" / "catalog.json").write_text('{"profiles": ["x"]}', encoding="utf-8")
    after = emit_evidence(root=tree, manifest=manifest())["evidence"]["catalog"]["sha256"]
    assert before != after


def test_hash_integrity_is_claimed_but_authenticity_never_is(tree):
    """The distinction the whole module turns on."""
    emitted = emit_evidence(root=tree, manifest=manifest())
    assert emitted["hash_integrity"] is True
    assert emitted["content_authenticity"] == "unverified"
    assert emitted["execution_allowed"] is False


def test_authenticity_stays_unverified_even_when_everything_succeeds(tree):
    for payload in (emit_evidence(root=tree, manifest=manifest()),
                    inspect_sources(root=tree, manifest=manifest()),
                    build_snapshot(root=tree, manifest=manifest(), binding=BINDING)):
        assert payload["content_authenticity"] == "unverified"


def test_hash_integrity_is_false_when_any_class_failed(tree):
    spec = manifest()
    spec["catalog"]["document"] = "configs/missing.json"
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["hash_integrity"] is False
    assert emitted["unavailable"]["catalog"] == ["source_missing"]


# --- refusals: traversal, absolute paths, aliases, streams --------------------


@pytest.mark.parametrize("bad,reason", [
    ("../outside.json", "path_traversal_forbidden"),
    ("configs/../../outside.json", "path_traversal_forbidden"),
    ("/etc/passwd", "absolute_source_path_forbidden"),
    ("C:/Windows/system.ini", "absolute_source_path_forbidden"),
    ("configs/catalog.json:stream", "alternate_data_stream_forbidden"),
    ("configs/PROGRA~1/x.json", "ambiguous_windows_alias:PROGRA~1"),
    ("configs/trailing./x.json", "ambiguous_windows_alias:trailing."),
    ("configs/missing.json", "source_missing"),
])
def test_unsafe_or_missing_source_paths_are_refused(tree, bad, reason):
    spec = manifest()
    spec["catalog"]["document"] = bad
    emitted = emit_evidence(root=tree, manifest=spec)
    assert "catalog" not in emitted["evidence"]
    assert emitted["unavailable"]["catalog"] == [reason]


def test_a_directory_is_not_a_document(tree):
    spec = manifest()
    spec["catalog"]["document"] = "configs"
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["unavailable"]["catalog"] == ["source_is_not_a_regular_file"]


def test_an_oversized_source_is_refused(tree):
    big = tree / "configs" / "big.bin"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    spec = manifest()
    spec["catalog"]["document"] = "configs/big.bin"
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["unavailable"]["catalog"] == ["source_exceeds_size_bound"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_a_symlinked_source_is_refused(tree):
    target = tree / "configs" / "catalog.json"
    link = tree / "configs" / "linked.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this machine")
    spec = manifest()
    spec["catalog"]["document"] = "configs/linked.json"
    emitted = emit_evidence(root=tree, manifest=spec)
    assert "catalog" not in emitted["evidence"]
    assert emitted["unavailable"]["catalog"][0].startswith("reparse_point_in_path:")


def test_changed_during_read_is_detected(tree, monkeypatch):
    """A writer that moves the file mid-read must not yield a digest."""
    import tools.bridge_policy_evidence as mod
    target = tree / "configs" / "catalog.json"
    real_fstat = mod.os.fstat

    def moved(fd):
        info = real_fstat(fd)
        return os.stat_result((info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                               info.st_uid, info.st_gid, info.st_size + 1,
                               info.st_atime, info.st_mtime, info.st_ctime))

    monkeypatch.setattr(mod.os, "fstat", moved)
    spec = manifest()
    emitted = emit_evidence(root=tree, manifest=spec)
    assert "catalog" not in emitted["evidence"]
    assert emitted["unavailable"]["catalog"] == ["source_changed_during_read"]
    assert str(target)  # the file itself was never modified


# --- the root allowlist -------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "", "relative/path", 5, "//server/share"])
def test_a_bad_root_is_a_structural_error(bad):
    with pytest.raises(InputError):
        allowlisted_root(bad)


def test_a_nonexistent_root_is_refused(tmp_path):
    with pytest.raises(InputError):
        allowlisted_root(tmp_path / "nope")


def test_a_file_is_not_a_root(tree):
    with pytest.raises(InputError):
        allowlisted_root(tree / "configs" / "catalog.json")


def test_root_is_echoed_and_normalised(tree):
    assert emit_evidence(root=tree, manifest=manifest())["root"] == str(tree).replace("\\", "/")


# --- no fabrication -----------------------------------------------------------


def test_an_undeclared_class_is_unavailable_not_invented(tree):
    spec = manifest()
    del spec["qualification"]
    emitted = emit_evidence(root=tree, manifest=spec)
    assert "qualification" not in emitted["evidence"]
    assert emitted["unavailable"]["qualification"] == ["not_declared_in_manifest"]


def test_empty_manifest_yields_no_evidence_at_all(tree):
    emitted = emit_evidence(root=tree, manifest={})
    assert emitted["evidence"] == {}
    assert set(emitted["unavailable"]) == set(FILE_BACKED_CLASSES)
    assert emitted["hash_integrity"] is False


def test_native_is_never_file_backed(tree):
    assert "native" not in FILE_BACKED_CLASSES
    with pytest.raises(InputError):
        emit_evidence(root=tree, manifest={"native": {"document": "configs/catalog.json"}})
    assert "never read from disk" in inspect_sources(root=tree, manifest={})["native"]


def test_a_model_label_is_not_qualification_evidence(tree):
    spec = manifest()
    spec["qualification"] = {"document": "reports/qualification.json",
                             "model": "some-model-name"}
    emitted = emit_evidence(root=tree, manifest=spec)
    assert "qualification" not in emitted["evidence"]
    assert emitted["unavailable"]["qualification"] == ["qualification_evidence_ids_missing"]


@pytest.mark.parametrize("drop,reason", [
    ("evidence_ids", "qualification_evidence_ids_missing"),
    ("verdicts", "qualification_verdicts_missing"),
])
def test_qualification_needs_measured_fields(tree, drop, reason):
    spec = manifest()
    del spec["qualification"][drop]
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["unavailable"]["qualification"] == [reason]


@pytest.mark.parametrize("drop,reason", [
    ("profile_id", "profile_id_missing"),
    ("authorization_ref", "profile_authorization_ref_missing"),
])
def test_profile_needs_its_identity_fields(tree, drop, reason):
    spec = manifest()
    del spec["profile"][drop]
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["unavailable"]["profile"] == [reason]


@pytest.mark.parametrize("documents", [[], "configs/policy/constitution.yaml", None, {}])
def test_policy_needs_a_nonempty_document_list(tree, documents):
    spec = manifest()
    spec["policy"]["documents"] = documents
    emitted = emit_evidence(root=tree, manifest=spec)
    assert emitted["unavailable"]["policy"] == ["policy_documents_missing"]


def test_manifest_must_be_an_object(tree):
    for bad in (None, "manifest", 5, []):
        with pytest.raises(InputError):
            emit_evidence(root=tree, manifest=bad)


# --- the join: emitter -> epoch builder -> admission ---------------------------


def admission_request(epochs):
    return {
        "binding": deepcopy(BINDING),
        "epochs": epochs,
        "hold": False,
        "cancelled": False,
        "owner": {"verified": True, "principal": "fable-5",
                  "verification_ref": "claim/fable-5/autonomy-evidence-source-20260925"},
        "current_profile": {"profile_id": "fable-producer/default", "authorized": True,
                            "healthy": True, "authorization_ref": "operator/2026-09-25"},
        "failure": None,
    }


def test_a_complete_tree_produces_a_complete_snapshot_and_keeps(tree):
    built = build_snapshot(root=tree, manifest=manifest(), binding=BINDING)
    snap = built["snapshot"]
    assert snap["schema"] == EPOCH_SNAPSHOT_SCHEMA
    assert set(snap["epochs"]) == set(EPOCH_FIELDS)
    assert snap["complete"] is True
    assert admit(admission_request(snap["epochs"]))["verdict"] == KEEP


@pytest.mark.parametrize("missing", ["catalog", "qualification", "profile"])
def test_an_unevidenced_class_parks_admission_end_to_end(tree, missing):
    """The end-to-end safety property: what we cannot evidence, we do not run."""
    spec = manifest()
    del spec[missing]
    built = build_snapshot(root=tree, manifest=spec, binding=BINDING)
    assert missing in built["source"]["unavailable"]
    assert f"{missing}_epoch" not in built["snapshot"]["epochs"]
    result = admit(admission_request(built["snapshot"]["epochs"]))
    assert result["verdict"] == PARK
    assert f"epoch_unknown:{missing}_epoch" in result["reasons"]


def test_rereading_an_unchanged_tree_is_deterministic(tree):
    assert (build_snapshot(root=tree, manifest=manifest(), binding=BINDING)["snapshot"]
            == build_snapshot(root=tree, manifest=manifest(), binding=BINDING)["snapshot"])


def test_inspect_sources_reports_availability_honestly(tree):
    spec = manifest()
    del spec["qualification"]
    spec["catalog"]["document"] = "configs/missing.json"
    report = inspect_sources(root=tree, manifest=spec)
    assert report["available"] == ["policy", "profile"]
    assert report["unavailable"]["qualification"] == ["not_declared_in_manifest"]
    assert report["unavailable"]["catalog"] == ["source_missing"]
