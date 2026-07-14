"""Adversarial matrix for S2 per-query receipt coverage (frozen W1A v3 + rco-1 MF-1a
+ tools W1C F1/F2/F3 + lead/operator W1B cross-contract blockers 1/2/3).

Each forgery/omission/malformed case MUST return coverage_present=False; the happy path MUST
return True (liveness). The corpus digest is recomputed internally, so the happy header binds via
the module's own canonical_corpus_digest; route-evidence rows are the strict 3-key shape.
"""

import pytest

from waggledance.core.magma.chat_served_per_query_coverage import (
    MEASUREMENT_MARKER,
    NORMALIZATION_VERSION,
    RUN_CONTEXT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    canonical_corpus_digest,
    derive_per_query_receipt_coverage,
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
