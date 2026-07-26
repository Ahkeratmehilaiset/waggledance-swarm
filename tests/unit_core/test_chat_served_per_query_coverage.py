"""Adversarial matrix for S2 per-query receipt coverage (frozen W1A v3 + rco-1 MF-1a
+ tools W1C F1/F2/F3 + lead/operator W1B cross-contract blockers 1/2/3).

Each forgery/omission/malformed case MUST return coverage_present=False; the happy path MUST
return True (liveness). The corpus digest is recomputed internally, so the happy header binds via
the module's own canonical_corpus_digest; route-evidence rows are the strict 3-key shape.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.chat_query_route_evidence import canonical_query_digest
from waggledance.core.magma.chat_served_ledger import (
    GENESIS_PREV_HASH,
    append_entry,
    new_gap_terminal,
    new_receipt_terminal,
    new_served_pending,
)

from waggledance.core.magma.chat_served_per_query_coverage import (
    MEASUREMENT_MARKER,
    NORMALIZATION_VERSION,
    ReceiptManifestIndex,
    RUN_CONTEXT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    canonical_corpus_digest,
    derive_per_query_receipt_coverage,
    derive_per_query_receipt_coverage_from_artifacts,
    _path_is_link,
)
from waggledance.core.magma.chat_served_receipt import (
    CHAIN_ID as CHAT_CHAIN_ID,
    build_chat_served_receipt,
    build_chat_served_summary,
    write_chat_served_receipt_bundle,
)
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


def _q(c):  # a valid sha256 query digest
    return "sha256:" + (c * 64)


def _ref(value):
    return value if value.startswith("sha256:") else _q(value[-1])


def _re(served_id, query_digest, normalization_version=NORMALIZATION_VERSION):
    return {"served_id": served_id, "query_digest": query_digest, "normalization_version": normalization_version}


def _receipt_term(served_id, receipt_ref):
    return {"entry_type": "receipt", "served_id": served_id, "receipt_ref": _ref(receipt_ref)}


def _gap_term(served_id, gap_reason="receipt_build_failed"):
    return {"entry_type": "gap", "served_id": served_id, "gap_reason": gap_reason}


def _ctx_for(route_evidence):
    digests = [r["query_digest"] for r in route_evidence if isinstance(r, dict) and r.get("query_digest")]
    return {
        "head_commit_sha": "a" * 40,
        "corpus_digest": canonical_corpus_digest(digests),
        "schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": "sha256:" + "e" * 64,
    }


class _Store:
    def __init__(self):
        self._r = {}

    def add_genuine(self, ref, query_digest):
        ref = _ref(ref)
        self._r[ref] = {"_content_hash": ref, "_verify_ok": True, "_query_digest": query_digest}
        return ref

    def add_forged(self, ref, query_digest, *, content_hash=None, verify_ok=True):
        ref = _ref(ref)
        self._r[ref] = {
            "_content_hash": content_hash if content_hash is not None else ref,
            "_verify_ok": verify_ok,
            "_query_digest": query_digest,
        }
        return ref

    def resolve(self, ref):
        return self._r.get(ref)

    @staticmethod
    def verify(receipt):
        return bool(receipt.get("_verify_ok"))

    @staticmethod
    def content_hash(receipt):
        return receipt.get("_content_hash")

    @staticmethod
    def query_digest(receipt):
        return receipt.get("_query_digest")


def _derive(route_evidence, terminals, store, *, run_header=None, claim_context=None,
            pending_append_failures=0, chain_ok=True, callback_overrides=None):
    ctx = _ctx_for(route_evidence)
    callbacks = {
        "resolve_receipt": store.resolve,
        "verify_receipt": store.verify,
        "content_hash": store.content_hash,
        "receipt_query_digest": store.query_digest,
    }
    callbacks.update(callback_overrides or {})
    return derive_per_query_receipt_coverage(
        route_evidence=route_evidence,
        run_header=run_header if run_header is not None else dict(ctx),
        claim_context=claim_context if claim_context is not None else dict(ctx),
        ledger_terminals=terminals,
        **callbacks,
        pending_append_failures=pending_append_failures,
        chain_ok=chain_ok,
    )


# --- liveness -------------------------------------------------------------------------------

def test_happy_path_all_bound_is_present():
    store = _Store()
    store.add_genuine("sha256:" + "1" * 64, _q("a"))
    store.add_genuine("sha256:" + "2" * 64, _q("b"))
    re = [_re("s1", _q("a")), _re("s2", _q("b"))]
    terms = [_receipt_term("s1", "sha256:" + "1" * 64), _receipt_term("s2", "sha256:" + "2" * 64)]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is True and rep.reason is None
    assert rep.verified_bound == 2 and rep.corpus_size == 2
    assert rep.evidence_ok and rep.context_ok and rep.corpus_bound
    assert rep.schema_version == SCHEMA_VERSION and rep.measurement_marker == MEASUREMENT_MARKER


# --- blocker 2: internal canonical corpus digest --------------------------------------------

def test_corpus_digest_matches_internal_canonical():
    # a caller cannot substitute a favorable corpus: the header digest must equal the INTERNAL recompute
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["corpus_digest"] = "sha256:" + "d" * 64  # not the canonical recompute
    rep = _derive(re, terms, store, run_header=header, claim_context=dict(header))
    assert rep.coverage_present is False and rep.corpus_bound is False
    assert rep.reason == "corpus_digest_unbound_from_route_evidence"


# --- blocker 3: fail-closed row validation (no silent filter) -------------------------------

def test_malformed_row_shape_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [{"served_id": "s1", "query_digest": _q("a")}]  # missing normalization_version key
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.evidence_ok is False
    assert rep.reason.startswith("malformed_route_evidence")


def test_invalid_query_digest_is_false():
    store = _Store()
    re = [_re("s1", "not-a-sha256")]
    rep = _derive(re, [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_query_digest_with_trailing_newline_is_false():
    store = _Store()
    rep = _derive([_re("s1", _q("a") + "\n")], [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_empty_served_id_is_false():
    store = _Store()
    re = [_re("", _q("a"))]
    rep = _derive(re, [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_numeric_served_id_is_false():
    store = _Store()
    rep = _derive([_re(7, _q("a"))], [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_path_unsafe_served_id_is_false():
    store = _Store()
    rep = _derive([_re("s1\n", _q("a"))], [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_wrong_per_row_normalization_is_false():
    store = _Store()
    re = [_re("s1", _q("a"), normalization_version="wd.chat_query_normalization.vX")]
    rep = _derive(re, [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_duplicate_served_id_is_false():
    store = _Store()
    re = [_re("s1", _q("a")), _re("s1", _q("b"))]  # duplicate served_id
    rep = _derive(re, [], store)
    assert rep.coverage_present is False and rep.evidence_ok is False


def test_repeated_query_digest_with_one_receipt_per_served_event_is_true():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    store.add_genuine("r2", _q("a"))
    re = [_re("s1", _q("a")), _re("s2", _q("a"))]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]

    rep = _derive(re, terms, store)

    assert rep.coverage_present is True and rep.evidence_ok is True
    assert rep.corpus_size == 2 and rep.verified_bound == 2
    assert rep.duplicate_terminal == 0 and rep.duplicate_query_terminal == 0


def test_repeated_query_digest_cannot_reuse_receipt_across_served_events():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a")), _re("s2", _q("a"))]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r1")]

    rep = _derive(re, terms, store)

    assert rep.coverage_present is False
    assert rep.verified_bound == 0 and rep.forged_or_unbound == 2
    assert rep.reason == "duplicate_receipt_ref:1"


def test_repeated_query_digest_missing_one_served_event_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a")), _re("s2", _q("a"))]

    rep = _derive(re, [_receipt_term("s1", "r1")], store)

    assert rep.coverage_present is False and rep.evidence_ok is True
    assert rep.reason is not None and "bijection_unmet" in rep.reason
    assert rep.missing == 1


def test_repeated_query_digest_duplicate_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    store.add_genuine("r2", _q("a"))
    re = [_re("s1", _q("a")), _re("s2", _q("a"))]
    terms = [
        _receipt_term("s1", "r1"),
        _gap_term("s1"),
        _receipt_term("s2", "r2"),
    ]

    rep = _derive(re, terms, store)

    assert rep.coverage_present is False
    assert rep.duplicate_terminal == 1


# --- rco-2 R1 bijection: missing query ------------------------------------------------------

def test_missing_query_bijection_unmet_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    store.add_genuine("r2", _q("b"))
    re = [_re("s1", _q("a")), _re("s2", _q("b")), _re("s3", _q("c"))]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]  # qc unbound
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and "bijection_unmet" in rep.reason and rep.missing == 1


# --- rco-1 MF-1 / MF-1a: forgery keystone ---------------------------------------------------

def test_nonexistent_receipt_is_false():
    store = _Store()
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "sha256:" + "9" * 64)]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_cross_query_receipt_is_false():
    store = _Store()
    store.add_genuine("r1", _q("z"))  # bound to a DIFFERENT query
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_content_mismatch_is_false():
    store = _Store()
    store.add_forged("r1", _q("a"), content_hash="sha256:" + "f" * 64)
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_verify_fail_is_false():
    store = _Store()
    store.add_forged("r1", _q("a"), verify_ok=False)
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_truthy_non_bool_verifier_result_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    rep = _derive(
        [_re("s1", _q("a"))],
        [_receipt_term("s1", "r1")],
        store,
        callback_overrides={"verify_receipt": lambda _: "true"},
    )
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


@pytest.mark.parametrize(
    "callback_name",
    ["resolve_receipt", "content_hash", "verify_receipt", "receipt_query_digest"],
)
def test_callback_exception_fails_closed_for_served_event(callback_name):
    store = _Store()
    store.add_genuine("r1", _q("a"))

    def raise_callback(*_args):
        raise RuntimeError("injected callback failure")

    rep = _derive(
        [_re("s1", _q("a"))],
        [_receipt_term("s1", "r1")],
        store,
        callback_overrides={callback_name: raise_callback},
    )

    assert rep.coverage_present is False
    assert rep.verified_bound == 0 and rep.forged_or_unbound == 1
    assert rep.reason == "bijection_unmet:bound=0/corpus=1(gap=0,missing=0,forged=1)"


# --- rco-1 MF-2 / MF-3 ----------------------------------------------------------------------

def test_corpus_missing_entry_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a")), _re("s2", _q("b"))]  # qb no terminal
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.missing == 1


def test_pending_append_failure_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store, pending_append_failures=1)
    assert rep.coverage_present is False and "pending_append_failures" in rep.reason


@pytest.mark.parametrize("malformed", [True, -1, "1", 1.0, None])
def test_malformed_pending_append_failures_is_false(malformed):
    store = _Store()
    store.add_genuine("r1", _q("a"))
    rep = _derive(
        [_re("s1", _q("a"))],
        [_receipt_term("s1", "r1")],
        store,
        pending_append_failures=malformed,
    )
    assert rep.coverage_present is False
    assert rep.reason == "malformed_pending_append_failures"
    assert rep.pending_append_failures == 0


def test_gap_then_bound_duplicate_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_gap_term("s1"), _receipt_term("s1", "r1")]  # gap not papered by a later bound
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.duplicate_terminal == 1


def test_terminal_must_be_a_mapping_with_exact_keyset():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    malformed = _receipt_term("s1", "r1")
    malformed["extra"] = "smuggled"

    not_mapping = _derive(re, ["receipt"], store)
    extra_key = _derive(re, [malformed], store)

    assert not_mapping.coverage_present is False
    assert not_mapping.reason == "malformed_ledger_terminal:row_0_not_mapping"
    assert extra_key.coverage_present is False
    assert extra_key.reason == "malformed_ledger_terminal:row_0_receipt_keyset"


def test_terminal_rejects_numeric_or_path_unsafe_served_id():
    store = _Store()
    re = [_re("s1", _q("a"))]
    numeric = {"entry_type": "gap", "served_id": 7, "gap_reason": "receipt_build_failed"}
    newline = {"entry_type": "gap", "served_id": "s1\n", "gap_reason": "receipt_build_failed"}

    numeric_rep = _derive(re, [numeric], store)
    newline_rep = _derive(re, [newline], store)

    assert numeric_rep.reason == "malformed_ledger_terminal:row_0_served_id"
    assert newline_rep.reason == "malformed_ledger_terminal:row_0_served_id"


def test_terminal_rejects_malformed_receipt_ref_and_gap_reason():
    store = _Store()
    re = [_re("s1", _q("a"))]
    bad_ref = {"entry_type": "receipt", "served_id": "s1", "receipt_ref": 7}
    bad_gap = {"entry_type": "gap", "served_id": "s1", "gap_reason": "free-form"}

    assert _derive(re, [bad_ref], store).reason == (
        "malformed_ledger_terminal:row_0_receipt_ref"
    )
    assert _derive(re, [bad_gap], store).reason == (
        "malformed_ledger_terminal:row_0_gap_reason"
    )


def test_plain_gap_is_false():
    store = _Store()
    re = [_re("s1", _q("a"))]
    terms = [_gap_term("s1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.gapped == 1


def test_orphan_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1"), _receipt_term("sX", "r1")]  # sX not in corpus
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.orphan_terminals == 1


def test_broken_chain_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store, chain_ok=False)
    assert rep.coverage_present is False and rep.reason == "ledger_chain_broken"


def test_truthy_string_chain_state_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    rep = _derive(re, [_receipt_term("s1", "r1")], store, chain_ok="true")
    assert rep.coverage_present is False and rep.reason == "ledger_chain_broken"
    assert rep.chain_ok is False


def test_chain_state_defaults_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    ctx = _ctx_for(re)
    rep = derive_per_query_receipt_coverage(
        route_evidence=re,
        run_header=ctx,
        claim_context=dict(ctx),
        ledger_terminals=[_receipt_term("s1", "r1")],
        resolve_receipt=store.resolve,
        verify_receipt=store.verify,
        content_hash=store.content_hash,
        receipt_query_digest=store.query_digest,
    )
    assert rep.coverage_present is False and rep.reason == "ledger_chain_broken"


def test_empty_corpus_is_false():
    store = _Store()
    rep = _derive([], [], store)
    assert rep.coverage_present is False and rep.reason == "empty_corpus"


# --- blocker 1 + tools F1/F2 + rco-2 R2: run-context header binding --------------------------

def test_header_uses_run_context_schema_not_report_schema():
    # a header carrying the COVERAGE-REPORT schema (the old bug) must now reject
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["schema_version"] = SCHEMA_VERSION  # report schema, not the run-context schema
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_stale_head_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["head_commit_sha"] = "b" * 40
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.reason == "stale_or_wrong_measurement_context"


def test_wrong_run_id_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["run_id"] = "sha256:" + "9" * 64
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_header_digest_with_trailing_newline_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    header = _ctx_for(re)
    header["run_id"] += "\n"
    rep = _derive(re, [_receipt_term("s1", "r1")], store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_malformed_but_equal_header_is_false():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    bad = {
        "head_commit_sha": "bad",
        "corpus_digest": "bad",
        "schema_version": RUN_CONTEXT_SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": "bad",
    }
    rep = derive_per_query_receipt_coverage(
        route_evidence=re, run_header=bad, claim_context=bad, ledger_terminals=terms,
        resolve_receipt=store.resolve, verify_receipt=store.verify,
        content_hash=store.content_hash, receipt_query_digest=store.query_digest,
    )
    assert rep.coverage_present is False and rep.context_ok is False


def test_missing_header_field_fails_closed():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    re = [_re("s1", _q("a"))]
    terms = [_receipt_term("s1", "r1")]
    header = {"head_commit_sha": "a" * 40, "corpus_digest": canonical_corpus_digest([_q("a")])}
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


# --- claim-safety posture -------------------------------------------------------------------

def test_report_has_no_claim_safe_field_and_is_marked_measurement_only():
    store = _Store()
    store.add_genuine("r1", _q("a"))
    rep = _derive([_re("s1", _q("a"))], [_receipt_term("s1", "r1")], store)
    assert rep.measurement_marker == "measurement_not_a_correctness_gate"
    assert not any("claim_safe" in f for f in rep._fields)


# --- read-only production artifact adapter -------------------------------------------------

_ARTIFACT_NOW = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
_LEDGER_TS = "2026-07-20T08:00:00.000000Z"


def _chat_summary(query: str) -> dict:
    return build_chat_served_summary(
        query=query,
        response="private response DO_NOT_LEAK",
        route_type="solver",
        source="solver",
        confidence=0.9,
        latency_ms=1.0,
        cached=False,
        round_table=False,
        agent_id=None,
        language="fi",
        profile="HOME",
        world_snapshot_ref="not_applicable",
        route_stage_trace=[],
    )


def _write_chat_bundle(
    receipt_root: Path,
    *,
    leaf: str,
    query: str,
    ordinal: int,
) -> tuple[Path, dict]:
    report = write_chat_served_receipt_bundle(
        out_dir=receipt_root / leaf,
        summary_payload=_chat_summary(query),
        now_utc=_ARTIFACT_NOW + timedelta(seconds=ordinal),
        verify_manifest=verify_manifest,
        ordinal=ordinal,
    )
    return Path(report["manifest"]), report["receipt"]


def _pending_metadata(
    query: str,
    *,
    normalization_version: str = NORMALIZATION_VERSION,
) -> dict[str, str]:
    return {
        "source": "solver",
        "route_type": "solver",
        "language": "fi",
        "profile": "HOME",
        "world_snapshot_ref": "not_applicable",
        "query_digest": canonical_query_digest(query),
        "normalization_version": normalization_version,
    }


def _write_artifact_ledger(
    path: Path,
    *,
    pendings: list[tuple[str, dict[str, str]]],
    terminals: list[tuple[str, str, str]],
) -> str:
    head = GENESIS_PREV_HASH
    for served_id, metadata in pendings:
        entry = new_served_pending(served_id, head, _LEDGER_TS, metadata)
        head = append_entry(str(path), entry, fsync=False)
    for terminal_type, served_id, value in terminals:
        if terminal_type == "receipt":
            entry = new_receipt_terminal(served_id, head, _LEDGER_TS, value)
        else:
            entry = new_gap_terminal(served_id, head, _LEDGER_TS, value)
        head = append_entry(str(path), entry, fsync=False)
    return head


def _window_index(
    ledger_path: Path,
    *,
    final_head: str,
    manifests: tuple[Path, ...],
) -> ReceiptManifestIndex:
    return ReceiptManifestIndex(
        ledger_start_offset=0,
        ledger_final_offset=ledger_path.stat().st_size,
        ledger_start_prev_hash=GENESIS_PREV_HASH,
        ledger_final_head=final_head,
        manifest_paths=manifests,
    )


def _artifact_case(
    tmp_path: Path,
    bindings: list[tuple[str, str, str]],
) -> dict:
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(parents=True)
    pendings: list[tuple[str, dict[str, str]]] = []
    terminals: list[tuple[str, str, str]] = []
    manifests: list[Path] = []
    route_evidence: list[dict] = []
    for ordinal, (served_id, pending_query, receipt_query) in enumerate(
        bindings, 1
    ):
        manifest, receipt = _write_chat_bundle(
            receipt_root,
            leaf=f"bundle-{ordinal}",
            query=receipt_query,
            ordinal=ordinal,
        )
        query_digest = canonical_query_digest(pending_query)
        pendings.append((served_id, _pending_metadata(pending_query)))
        terminals.append(("receipt", served_id, sha256_digest(receipt)))
        manifests.append(manifest)
        route_evidence.append(_re(served_id, query_digest))

    ledger_path = tmp_path / "ledger.jsonl"
    head = _write_artifact_ledger(
        ledger_path, pendings=pendings, terminals=terminals
    )
    context = _ctx_for(route_evidence)
    return {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(
            ledger_path, final_head=head, manifests=tuple(manifests)
        ),
        "context": context,
    }


def _artifact_report(
    case: dict,
    *,
    index: ReceiptManifestIndex | None = None,
    verifier=verify_manifest,
    receipt_root: Path | None = None,
):
    return derive_per_query_receipt_coverage_from_artifacts(
        ledger_path=case["ledger_path"],
        receipt_root=(
            receipt_root if receipt_root is not None else case["receipt_root"]
        ),
        receipt_manifest_index=index if index is not None else case["index"],
        run_header=dict(case["context"]),
        claim_context=dict(case["context"]),
        verify_manifest=verifier,
    )


def _live_bundle_artifact_path(case: dict, field: str) -> Path:
    manifest_path = case["index"].manifest_paths[0]
    if field == "manifest":
        return manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_path.parent / manifest["entries"][0][field]


def test_artifact_adapter_exact_pending_and_receipt_identity_is_present(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])

    report = _artifact_report(case)

    assert report.coverage_present is True
    assert report.verified_bound == report.corpus_size == 1


def test_artifact_adapter_rejects_q1_pending_bound_to_valid_q2_receipt(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query two")])

    report = _artifact_report(case)

    assert report.coverage_present is False
    assert report.verified_bound == 0 and report.forged_or_unbound == 1


def test_artifact_adapter_rejects_swapped_receipts(tmp_path):
    case = _artifact_case(
        tmp_path,
        [
            ("s1", "query one", "query two"),
            ("s2", "query two", "query one"),
        ],
    )

    report = _artifact_report(case)

    assert report.coverage_present is False
    assert report.verified_bound == 0 and report.forged_or_unbound == 2


def test_artifact_adapter_allows_repeated_query_with_unique_ids_and_receipts(
    tmp_path,
):
    case = _artifact_case(
        tmp_path,
        [("s1", "same query", "same query"), ("s2", "same query", "same query")],
    )

    report = _artifact_report(case)

    assert report.coverage_present is True
    assert report.verified_bound == report.corpus_size == 2


def test_artifact_adapter_rejects_receipt_reuse(tmp_path):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    manifest, receipt = _write_chat_bundle(
        receipt_root, leaf="only", query="same query", ordinal=1
    )
    receipt_ref = sha256_digest(receipt)
    ledger_path = tmp_path / "ledger.jsonl"
    head = _write_artifact_ledger(
        ledger_path,
        pendings=[
            ("s1", _pending_metadata("same query")),
            ("s2", _pending_metadata("same query")),
        ],
        terminals=[
            ("receipt", "s1", receipt_ref),
            ("receipt", "s2", receipt_ref),
        ],
    )
    context = _ctx_for(
        [
            _re("s1", canonical_query_digest("same query")),
            _re("s2", canonical_query_digest("same query")),
        ]
    )
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(
            ledger_path, final_head=head, manifests=(manifest,)
        ),
        "context": context,
    }

    report = _artifact_report(case)

    assert report.coverage_present is False
    assert report.duplicate_terminal == 0
    assert report.reason == "duplicate_receipt_ref:1"


def test_artifact_adapter_rejects_missing_and_orphan_indexed_bundles(tmp_path):
    missing_case = _artifact_case(
        tmp_path / "missing", [("s1", "query one", "query one")]
    )
    missing_index = missing_case["index"]._replace(manifest_paths=())
    assert _artifact_report(
        missing_case, index=missing_index
    ).coverage_present is False

    orphan_case = _artifact_case(
        tmp_path / "orphan", [("s1", "query one", "query one")]
    )
    extra_manifest, _ = _write_chat_bundle(
        orphan_case["receipt_root"],
        leaf="orphan",
        query="orphan query",
        ordinal=99,
    )
    orphan_index = orphan_case["index"]._replace(
        manifest_paths=orphan_case["index"].manifest_paths + (extra_manifest,)
    )
    assert _artifact_report(
        orphan_case, index=orphan_index
    ).coverage_present is False


@pytest.mark.parametrize("terminal", [None, ("gap", "s1", "receipt_build_failed")])
def test_artifact_adapter_rejects_unresolved_or_gapped_pending(tmp_path, terminal):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    terminals = [] if terminal is None else [terminal]
    head = _write_artifact_ledger(
        ledger_path,
        pendings=[("s1", _pending_metadata("query one"))],
        terminals=terminals,
    )
    context = _ctx_for([_re("s1", canonical_query_digest("query one"))])
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(ledger_path, final_head=head, manifests=()),
        "context": context,
    }

    report = _artifact_report(case)

    assert report.coverage_present is False
    assert report.missing + report.gapped == 1


def test_artifact_adapter_rejects_torn_or_wrong_boundary_ledger(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    wrong_head = case["index"]._replace(ledger_final_head=_q("f"))
    assert _artifact_report(case, index=wrong_head).coverage_present is False

    with case["ledger_path"].open("ab") as handle:
        handle.write(b'{"torn"')
    torn = case["index"]._replace(
        ledger_final_offset=case["ledger_path"].stat().st_size
    )
    assert _artifact_report(case, index=torn).coverage_present is False


@pytest.mark.parametrize(
    "metadata",
    [
        {"source": "solver"},
        {
            **_pending_metadata("query one"),
            "normalization_version": "wd.chat_query_normalization.v0",
        },
    ],
)
def test_artifact_adapter_rejects_legacy_or_malformed_pending_identity(
    tmp_path, metadata
):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    head = _write_artifact_ledger(
        ledger_path, pendings=[("s1", metadata)], terminals=[]
    )
    context = _ctx_for([_re("s1", canonical_query_digest("query one"))])
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(ledger_path, final_head=head, manifests=()),
        "context": context,
    }

    report = _artifact_report(case)

    assert report.coverage_present is False
    assert report.evidence_ok is False


def test_artifact_adapter_rejects_terminal_ref_content_hash_mismatch(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    entries = case["ledger_path"].read_text(encoding="utf-8").splitlines()
    pending = json.loads(entries[0])
    case["ledger_path"].unlink()
    append_entry(str(case["ledger_path"]), pending, fsync=False)
    forged = new_receipt_terminal("s1", pending["entry_hash"], _LEDGER_TS, _q("f"))
    head = append_entry(str(case["ledger_path"]), forged, fsync=False)
    index = case["index"]._replace(
        ledger_final_offset=case["ledger_path"].stat().st_size,
        ledger_final_head=head,
    )

    report = _artifact_report(case, index=index)

    assert report.coverage_present is False
    assert report.forged_or_unbound == 1


def test_artifact_adapter_independently_rejects_payload_digest_mismatch(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    manifest_path = case["index"].manifest_paths[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload_path = manifest_path.parent / manifest["entries"][0]["payload"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["query_digest"] = canonical_query_digest("query two")
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    report = _artifact_report(
        case,
        verifier=lambda _path: {"ok": True, "receipt_count": 1, "errors": []},
    )

    assert report.coverage_present is False
    assert report.forged_or_unbound == 1


@pytest.mark.parametrize(
    "verifier_report",
    [
        {"ok": False, "receipt_count": 1, "errors": []},
        {"ok": "true", "receipt_count": 1, "errors": []},
        {"ok": True, "receipt_count": True, "errors": []},
        {"ok": True, "receipt_count": 1, "errors": ["poison"]},
        None,
    ],
)
def test_artifact_adapter_rejects_false_truthy_or_malformed_verifier_report(
    tmp_path, verifier_report
):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])

    report = _artifact_report(case, verifier=lambda _path: verifier_report)

    assert report.coverage_present is False
    assert report.forged_or_unbound == 1


def _write_generic_bundle(receipt_root: Path, query_digest: str) -> tuple[Path, dict]:
    payload = {"action": "generic", "query_digest": query_digest}
    evaluation = build_evaluation_result(
        case_id="case:generic:one",
        subject_type="counterfactual",
        target_payload=payload,
        risk_class="internal_memory",
        expected_gate="review",
        actual_gate="review",
        verifier_path=["unit_test"],
        solver_selection=["fixture_solver"],
        policy_version="policy:generic:v1",
        charter_version="charter:generic:v1",
        domain_threshold_version="threshold:generic:v1",
        verdict="pass",
        reason_codes=["generic_receipt"],
        confidence_score=1.0,
    )
    receipt = build_magma_receipt(
        event_id="magma:generic:001",
        ts_utc="2026-07-20T08:00:00Z",
        risk_class=evaluation["risk_class"],
        payload=payload,
        evaluation_result=evaluation,
        previous_receipt=None,
        policy_digest=_q("1"),
        charter_digest=_q("2"),
        rco_decision_digest=_q("3"),
        world_snapshot_digest=_q("4"),
        solver_contract_digest=_q("5"),
    )
    report = write_receipt_bundle(
        out_dir=receipt_root / "generic",
        chain_id="magma:generic:v0",
        entries=[
            ReceiptBundleEntry(
                label="generic",
                payload=payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )
    return Path(report["manifest"]), receipt


def test_artifact_adapter_rejects_verifier_valid_generic_bundle(tmp_path):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    query_digest = canonical_query_digest("query one")
    manifest, receipt = _write_generic_bundle(receipt_root, query_digest)
    assert verify_manifest(manifest)["ok"] is True
    ledger_path = tmp_path / "ledger.jsonl"
    head = _write_artifact_ledger(
        ledger_path,
        pendings=[("s1", _pending_metadata("query one"))],
        terminals=[("receipt", "s1", sha256_digest(receipt))],
    )
    context = _ctx_for([_re("s1", query_digest)])
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(
            ledger_path, final_head=head, manifests=(manifest,)
        ),
        "context": context,
    }

    assert _artifact_report(case).coverage_present is False


def test_artifact_adapter_rejects_verifier_valid_multi_entry_chat_bundle(tmp_path):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    first_payload, first_evaluation, first_receipt = build_chat_served_receipt(
        summary_payload=_chat_summary("query one"),
        now_utc=_ARTIFACT_NOW,
        ordinal=1,
    )
    second_payload, second_evaluation, second_receipt = build_chat_served_receipt(
        summary_payload=_chat_summary("query two"),
        now_utc=_ARTIFACT_NOW + timedelta(seconds=1),
        ordinal=2,
        previous_receipt=first_receipt,
    )
    bundle_report = write_receipt_bundle(
        out_dir=receipt_root / "multi",
        chain_id=CHAT_CHAIN_ID,
        entries=[
            ReceiptBundleEntry(
                "first", first_payload, first_evaluation, first_receipt
            ),
            ReceiptBundleEntry(
                "second", second_payload, second_evaluation, second_receipt
            ),
        ],
        verify_manifest=verify_manifest,
    )
    manifest = Path(bundle_report["manifest"])
    assert verify_manifest(manifest)["ok"] is True
    ledger_path = tmp_path / "ledger.jsonl"
    head = _write_artifact_ledger(
        ledger_path,
        pendings=[("s1", _pending_metadata("query two"))],
        terminals=[("receipt", "s1", sha256_digest(second_receipt))],
    )
    context = _ctx_for([_re("s1", canonical_query_digest("query two"))])
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": _window_index(
            ledger_path, final_head=head, manifests=(manifest,)
        ),
        "context": context,
    }

    assert _artifact_report(case).coverage_present is False


def test_artifact_adapter_rejects_receipt_root_escape(tmp_path):
    case = _artifact_case(
        tmp_path / "case", [("s1", "query one", "query one")]
    )
    outside = tmp_path / "outside-manifest.json"
    outside.write_text("{}", encoding="utf-8")
    escaped = case["index"]._replace(manifest_paths=(outside,))
    assert _artifact_report(case, index=escaped).coverage_present is False


def test_artifact_adapter_rejects_symlinked_manifest(tmp_path):
    case = _artifact_case(
        tmp_path / "case", [("s1", "query one", "query one")]
    )
    link = case["receipt_root"] / "manifest-link.json"
    try:
        link.symlink_to(case["index"].manifest_paths[0])
    except OSError:
        pytest.skip("symlink creation is unavailable")
    linked = case["index"]._replace(manifest_paths=(link,))
    assert _artifact_report(case, index=linked).coverage_present is False


def test_windows_reparse_attribute_is_recognized_without_pathlib_helper(
    monkeypatch,
):
    details = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=0x400,
    )
    monkeypatch.setattr(os, "lstat", lambda _path: details)

    assert _path_is_link(Path("receipt-root")) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_artifact_adapter_rejects_junction_bundle_without_pathlib_helper(
    tmp_path, monkeypatch
):
    case = _artifact_case(
        tmp_path / "case", [("s1", "query one", "query one")]
    )
    bundle_root = case["index"].manifest_paths[0].parent
    target = bundle_root.with_name(bundle_root.name + "-target")
    bundle_root.rename(target)
    created = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(bundle_root),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        target.rename(bundle_root)
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        monkeypatch.setattr(Path, "is_junction", None, raising=False)
        report = _artifact_report(case)
    finally:
        os.rmdir(bundle_root)

    assert report.coverage_present is False


def test_artifact_adapter_rejects_artifact_mutation_during_verification(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])

    def verify_then_mutate(manifest_path: Path):
        result = verify_manifest(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload_path = manifest_path.parent / manifest["entries"][0]["payload"]
        with payload_path.open("a", encoding="utf-8") as handle:
            handle.write(" ")
        return result

    assert _artifact_report(
        case, verifier=verify_then_mutate
    ).coverage_present is False


def test_artifact_adapter_verifier_exception_is_closed_and_never_leaks(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])

    def poisoned_verifier(_path: Path):
        raise RuntimeError("DO_NOT_LEAK C:\\private\\manifest query response")

    report = _artifact_report(case, verifier=poisoned_verifier)
    public = json.dumps(report._asdict(), sort_keys=True)

    assert report.coverage_present is False
    assert "DO_NOT_LEAK" not in public
    assert "private" not in public
    assert "C:\\\\private" not in public
    assert "query response" not in public
    assert "manifest" not in public
    assert "\\" not in public


def test_artifact_adapter_accepts_nonzero_anchored_window_and_binds_boundary(
    tmp_path,
):
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir()
    manifest, receipt = _write_chat_bundle(
        receipt_root, leaf="window", query="window query", ordinal=1
    )
    ledger_path = tmp_path / "ledger.jsonl"

    head = GENESIS_PREV_HASH
    prefix_pending = new_served_pending(
        "prefix", head, _LEDGER_TS, _pending_metadata("prefix query")
    )
    head = append_entry(str(ledger_path), prefix_pending, fsync=False)
    prefix_gap = new_gap_terminal(
        "prefix", head, _LEDGER_TS, "receipt_build_failed"
    )
    head = append_entry(str(ledger_path), prefix_gap, fsync=False)
    start_offset = ledger_path.stat().st_size
    start_prev_hash = head

    pending = new_served_pending(
        "window", head, _LEDGER_TS, _pending_metadata("window query")
    )
    head = append_entry(str(ledger_path), pending, fsync=False)
    terminal = new_receipt_terminal(
        "window", head, _LEDGER_TS, sha256_digest(receipt)
    )
    head = append_entry(str(ledger_path), terminal, fsync=False)
    context = _ctx_for(
        [_re("window", canonical_query_digest("window query"))]
    )
    index = ReceiptManifestIndex(
        ledger_start_offset=start_offset,
        ledger_final_offset=ledger_path.stat().st_size,
        ledger_start_prev_hash=start_prev_hash,
        ledger_final_head=head,
        manifest_paths=(manifest,),
    )
    case = {
        "ledger_path": ledger_path,
        "receipt_root": receipt_root,
        "index": index,
        "context": context,
    }

    assert _artifact_report(case).coverage_present is True
    wrong_anchor = index._replace(ledger_start_prev_hash=GENESIS_PREV_HASH)
    assert _artifact_report(case, index=wrong_anchor).coverage_present is False
    mid_line = index._replace(ledger_start_offset=start_offset + 1)
    assert _artifact_report(case, index=mid_line).coverage_present is False


def test_artifact_adapter_rejects_duplicate_json_keys_in_ledger(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    original = case["ledger_path"].read_bytes()
    needle = b'"served_id":"s1",'
    assert needle in original
    duplicated = original.replace(needle, needle + needle, 1)
    case["ledger_path"].write_bytes(duplicated)
    index = case["index"]._replace(
        ledger_final_offset=len(duplicated)
    )

    assert _artifact_report(case, index=index).coverage_present is False


@pytest.mark.parametrize("field", ["payload", "evaluation_result", "receipt"])
def test_artifact_adapter_rejects_symlinked_bundle_artifact(tmp_path, field):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    manifest_path = case["index"].manifest_paths[0]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = manifest_path.parent / manifest["entries"][0][field]
    real_path = artifact_path.with_name(artifact_path.name + ".real")
    artifact_path.rename(real_path)
    try:
        artifact_path.symlink_to(real_path)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    assert _artifact_report(case).coverage_present is False


def test_artifact_adapter_rejects_aba_verifier_swap_and_restore(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    original_manifest = case["index"].manifest_paths[0]
    manifest = json.loads(original_manifest.read_text(encoding="utf-8"))
    evaluation_path = (
        original_manifest.parent / manifest["entries"][0]["evaluation_result"]
    )
    valid_evaluation_bytes = evaluation_path.read_bytes()
    invalid_evaluation = json.loads(valid_evaluation_bytes)
    invalid_evaluation["verdict"] = "fail"
    evaluation_path.write_text(
        json.dumps(invalid_evaluation, sort_keys=True), encoding="utf-8"
    )
    invalid_evaluation_bytes = evaluation_path.read_bytes()
    assert verify_manifest(original_manifest)["ok"] is False

    def aba_verifier(_isolated_manifest: Path):
        evaluation_path.write_bytes(valid_evaluation_bytes)
        try:
            report = verify_manifest(original_manifest)
            assert report["ok"] is True
            return report
        finally:
            evaluation_path.write_bytes(invalid_evaluation_bytes)

    report = _artifact_report(case, verifier=aba_verifier)

    assert report.coverage_present is False
    assert evaluation_path.read_bytes() == invalid_evaluation_bytes


def test_artifact_adapter_revalidates_ledger_after_verifier_callback(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    original_ledger = case["ledger_path"].read_bytes()

    def verify_then_mutate_ledger(isolated_manifest: Path):
        report = verify_manifest(isolated_manifest)
        tampered = original_ledger.replace(
            b'"source":"solver"', b'"source":"xxxxxx"', 1
        )
        assert len(tampered) == len(original_ledger)
        assert tampered != original_ledger
        case["ledger_path"].write_bytes(tampered)
        return report

    report = _artifact_report(case, verifier=verify_then_mutate_ledger)

    assert report.coverage_present is False
    assert report.chain_ok is False


def test_artifact_adapter_normalizes_each_pathlike_once(tmp_path):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])

    class OncePath:
        def __init__(self, value: Path):
            self.value = str(value)
            self.calls = 0

        def __fspath__(self):
            self.calls += 1
            if self.calls != 1:
                raise RuntimeError("path normalized more than once")
            return self.value

    ledger = OncePath(case["ledger_path"])
    receipt_root = OncePath(case["receipt_root"])
    manifest = OncePath(case["index"].manifest_paths[0])
    index = case["index"]._replace(manifest_paths=(manifest,))
    report = derive_per_query_receipt_coverage_from_artifacts(
        ledger_path=ledger,
        receipt_root=receipt_root,
        receipt_manifest_index=index,
        run_header=dict(case["context"]),
        claim_context=dict(case["context"]),
        verify_manifest=verify_manifest,
    )

    assert report.coverage_present is True
    assert ledger.calls == receipt_root.calls == manifest.calls == 1


def test_artifact_adapter_runs_captured_canonical_verifier_before_rebound_seam(
    tmp_path, monkeypatch
):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    evaluation_path = _live_bundle_artifact_path(case, "evaluation_result")
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["verdict"] = "fail"
    evaluation_path.write_text(
        json.dumps(evaluation, sort_keys=True), encoding="utf-8"
    )

    from tools import verify_magma_receipt as verifier_module

    supplied_calls: list[Path] = []

    def rebound_accept_all(manifest_path: Path) -> dict:
        supplied_calls.append(manifest_path)
        return {"ok": True, "receipt_count": 1, "errors": []}

    monkeypatch.setattr(
        verifier_module, "verify_manifest", rebound_accept_all
    )
    report = _artifact_report(case, verifier=rebound_accept_all)

    assert report.coverage_present is False
    assert supplied_calls == []


def test_artifact_adapter_creates_no_scratch_inside_read_only_evidence_root(
    tmp_path, monkeypatch
):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    receipt_root = case["receipt_root"].resolve()
    original_mkdir = os.mkdir
    blocked_attempts: list[Path] = []

    def reject_evidence_root_mkdir(path, *args, **kwargs):
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(receipt_root)
        except ValueError:
            return original_mkdir(path, *args, **kwargs)
        blocked_attempts.append(candidate)
        raise PermissionError("receipt evidence root is read-only")

    monkeypatch.setattr(os, "mkdir", reject_evidence_root_mkdir)

    report = _artifact_report(case)

    assert report.coverage_present is True
    assert blocked_attempts == []
    assert not list(receipt_root.glob(".wd-per-query-verify-*"))


def test_artifact_adapter_rejects_callback_time_identical_byte_link_swap(
    tmp_path,
):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    payload_path = _live_bundle_artifact_path(case, "payload")
    original_bytes = payload_path.read_bytes()

    probe_target = case["receipt_root"] / "symlink-probe-target"
    probe_link = case["receipt_root"] / "symlink-probe-link"
    probe_target.write_bytes(b"probe")
    try:
        probe_link.symlink_to(probe_target.name)
    except OSError:
        probe_target.unlink()
        pytest.skip("symlink creation is unavailable")
    else:
        probe_link.unlink()
        probe_target.unlink()

    original_path = payload_path.with_name(payload_path.name + ".original")

    def verify_then_link_swap(isolated_manifest: Path):
        report = verify_manifest(isolated_manifest)
        payload_path.rename(original_path)
        payload_path.symlink_to(original_path.name)
        assert payload_path.read_bytes() == original_bytes
        return report

    report = _artifact_report(case, verifier=verify_then_link_swap)

    assert report.coverage_present is False
    assert payload_path.is_symlink()
    assert payload_path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    "field", ["manifest", "payload", "evaluation_result", "receipt"]
)
def test_artifact_adapter_rejects_callback_time_identical_byte_identity_swap(
    tmp_path, field
):
    case = _artifact_case(tmp_path, [("s1", "query one", "query one")])
    live_path = _live_bundle_artifact_path(case, field)
    original_bytes = live_path.read_bytes()
    original_identity = (live_path.stat().st_dev, live_path.stat().st_ino)

    def verify_then_replace(isolated_manifest: Path):
        report = verify_manifest(isolated_manifest)
        replacement = live_path.with_name(live_path.name + ".replacement")
        replacement.write_bytes(original_bytes)
        os.replace(replacement, live_path)
        return report

    report = _artifact_report(case, verifier=verify_then_replace)
    replaced_identity = (live_path.stat().st_dev, live_path.stat().st_ino)

    assert replaced_identity != original_identity
    assert live_path.read_bytes() == original_bytes
    assert report.coverage_present is False


def test_artifact_adapter_revalidates_earlier_manifest_after_later_callback(
    tmp_path,
):
    case = _artifact_case(
        tmp_path,
        [
            ("s1", "query one", "query one"),
            ("s2", "query two", "query two"),
        ],
    )
    earlier_manifest = case["index"].manifest_paths[0]
    original_bytes = earlier_manifest.read_bytes()
    callback_count = 0

    def verify_then_mutate_earlier(isolated_manifest: Path):
        nonlocal callback_count
        report = verify_manifest(isolated_manifest)
        callback_count += 1
        if callback_count == 2:
            earlier_manifest.write_bytes(original_bytes + b" ")
        return report

    report = _artifact_report(case, verifier=verify_then_mutate_earlier)

    assert callback_count == 2
    assert earlier_manifest.read_bytes() != original_bytes
    assert report.coverage_present is False


def test_artifact_adapter_revalidates_earlier_artifact_identity_after_later_callback(
    tmp_path,
):
    case = _artifact_case(
        tmp_path,
        [
            ("s1", "query one", "query one"),
            ("s2", "query two", "query two"),
        ],
    )
    earlier_payload = _live_bundle_artifact_path(case, "payload")
    original_bytes = earlier_payload.read_bytes()
    original_identity = (
        earlier_payload.stat().st_dev,
        earlier_payload.stat().st_ino,
    )
    callback_count = 0

    def verify_then_replace_earlier(isolated_manifest: Path):
        nonlocal callback_count
        report = verify_manifest(isolated_manifest)
        callback_count += 1
        if callback_count == 2:
            replacement = earlier_payload.with_name(
                earlier_payload.name + ".replacement"
            )
            replacement.write_bytes(original_bytes)
            os.replace(replacement, earlier_payload)
        return report

    report = _artifact_report(case, verifier=verify_then_replace_earlier)
    replaced_identity = (
        earlier_payload.stat().st_dev,
        earlier_payload.stat().st_ino,
    )

    assert callback_count == 2
    assert replaced_identity != original_identity
    assert earlier_payload.read_bytes() == original_bytes
    assert report.coverage_present is False


def test_artifact_adapter_two_receipt_final_revalidation_liveness(tmp_path):
    case = _artifact_case(
        tmp_path,
        [
            ("s1", "query one", "query one"),
            ("s2", "query two", "query two"),
        ],
    )
    callback_count = 0

    def observing_veto(isolated_manifest: Path):
        nonlocal callback_count
        callback_count += 1
        return verify_manifest(isolated_manifest)

    report = _artifact_report(case, verifier=observing_veto)

    assert callback_count == 2
    assert report.coverage_present is True
    assert report.verified_bound == report.corpus_size == 2
