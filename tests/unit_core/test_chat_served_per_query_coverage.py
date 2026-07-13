"""Adversarial matrix for S2 per-query receipt coverage (frozen W1A v3 + rco-1 MF-1a/RCO-1-C
+ tools W1C F1/F2/F3).

Each forgery/omission case MUST return coverage_present=False; the happy path MUST return
True (liveness, so no guard is vacuously failing). Fake resolver seams let us inject
nonexistent / content-mismatched / verify-failing / cross-query receipts directly, and a
deterministic corpus-digest fake lets us bind (or unbind) the header to the actual corpus.
"""

import hashlib

from waggledance.core.magma.chat_served_per_query_coverage import (
    MEASUREMENT_MARKER,
    NORMALIZATION_VERSION,
    SCHEMA_VERSION,
    derive_per_query_receipt_coverage,
)


def _corpus_digest(route_evidence):
    """Deterministic canonical digest of the corpus content (fake for tests; production binds to
    canonical.sha256_digest over the real route_evidence)."""
    items = sorted((str(x.get("served_id")), str(x.get("query_digest"))) for x in route_evidence)
    return "sha256:" + hashlib.sha256(repr(items).encode()).hexdigest()


def _ctx_for(route_evidence):
    return {
        "head_commit_sha": "a" * 40,
        "corpus_digest": _corpus_digest(route_evidence),
        "schema_version": SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": "sha256:" + "e" * 64,
    }


def _re(served_id, query_digest):
    return {"served_id": served_id, "query_digest": query_digest}


def _receipt_term(served_id, receipt_ref):
    return {"entry_type": "receipt", "served_id": served_id, "receipt_ref": receipt_ref}


def _gap_term(served_id, gap_reason="receipt_build_failed"):
    return {"entry_type": "gap", "served_id": served_id, "gap_reason": gap_reason}


class _Store:
    """Fake receipt store + seams. A 'genuine' receipt has content_hash==ref, verify_ok, query==q."""

    def __init__(self):
        self._r = {}

    def add_genuine(self, ref, query_digest):
        self._r[ref] = {"_content_hash": ref, "_verify_ok": True, "_query_digest": query_digest}
        return ref

    def add_forged(self, ref, query_digest, *, content_hash=None, verify_ok=True):
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
            pending_append_failures=0, chain_ok=True):
    ctx = _ctx_for(route_evidence)
    return derive_per_query_receipt_coverage(
        route_evidence=route_evidence,
        run_header=run_header if run_header is not None else dict(ctx),
        claim_context=claim_context if claim_context is not None else dict(ctx),
        ledger_terminals=terminals,
        resolve_receipt=store.resolve,
        verify_receipt=store.verify,
        content_hash=store.content_hash,
        receipt_query_digest=store.query_digest,
        corpus_content_digest=_corpus_digest,
        pending_append_failures=pending_append_failures,
        chain_ok=chain_ok,
    )


# --- liveness -------------------------------------------------------------------------------

def test_happy_path_all_bound_is_present():
    store = _Store()
    store.add_genuine("sha256:" + "1" * 64, "qd1")
    store.add_genuine("sha256:" + "2" * 64, "qd2")
    re = [_re("s1", "qd1"), _re("s2", "qd2")]
    terms = [_receipt_term("s1", "sha256:" + "1" * 64), _receipt_term("s2", "sha256:" + "2" * 64)]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is True
    assert rep.reason is None
    assert rep.verified_bound == 2 and rep.corpus_size == 2
    assert rep.context_ok and rep.corpus_bound
    assert rep.schema_version == SCHEMA_VERSION and rep.measurement_marker == MEASUREMENT_MARKER


# --- rco-2 R1: bijection ---------------------------------------------------------------------

def test_duplicate_masks_missing_is_false():
    # a duplicate bound entry must NOT mask a missing query (caught by per-query one-terminal rule).
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd2")
    re = [_re("s1", "qd1"), _re("s1b", "qd1"), _re("s2", "qd2"), _re("s3", "qd3")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s1b", "r1"), _receipt_term("s2", "r2")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert rep.duplicate_query_terminal >= 1


def test_missing_query_bijection_unmet_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd2")
    re = [_re("s1", "qd1"), _re("s2", "qd2"), _re("s3", "qd3")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]  # qd3 unbound
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert "bijection_unmet" in rep.reason and rep.missing == 1


# --- rco-1 MF-1 / MF-1a: forgery keystone ---------------------------------------------------

def test_nonexistent_receipt_is_false():
    store = _Store()
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "sha256:" + "9" * 64)]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_cross_query_receipt_is_false():
    store = _Store()
    store.add_genuine("r1", "qdX")  # bound to a DIFFERENT query
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_content_mismatch_is_false():
    store = _Store()
    store.add_forged("r1", "qd1", content_hash="sha256:" + "f" * 64)  # content_hash != ref
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_verify_fail_is_false():
    store = _Store()
    store.add_forged("r1", "qd1", verify_ok=False)  # fails verify_magma_receipt
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


# --- rco-1 MF-2: corpus denominator + fail-open durability ----------------------------------

def test_corpus_missing_entry_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1"), _re("s2", "qd2")]  # qd2 no terminal
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.missing == 1


def test_pending_append_failure_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store, pending_append_failures=1)
    assert rep.coverage_present is False and "pending_append_failures" in rep.reason


# --- rco-1 MF-3: terminal state (per served id AND per query) -------------------------------

def test_gap_then_bound_duplicate_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_gap_term("s1"), _receipt_term("s1", "r1")]  # a later bound must NOT paper the gap
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.duplicate_terminal == 1


def test_plain_gap_is_false():
    store = _Store()
    re = [_re("s1", "qd1")]
    terms = [_gap_term("s1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.gapped == 1


def test_two_terminals_one_query_is_false():  # tools W1C probe 3 (MF-3 at query scope)
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd1")  # second genuine receipt for the SAME query
    re = [_re("s1", "qd1"), _re("s2", "qd1")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert rep.duplicate_query_terminal == 1 and "duplicate_terminal_per_query" in rep.reason


# --- rco-2 R2 + tools F1/F2 + rco-1 RCO-1-C: header + corpus binding -------------------------

def test_stale_head_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["head_commit_sha"] = "b" * 40
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False
    assert rep.reason == "stale_or_wrong_measurement_context"


def test_wrong_normalization_version_is_false():  # tools W1C probe 1
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["normalization_version"] = "wd.chat_query_normalization.vX"
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_wrong_run_id_is_false():  # tools W1C probe 1
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = _ctx_for(re)
    header["run_id"] = "sha256:" + "9" * 64
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_malformed_but_equal_header_is_false():  # tools W1C probe 2
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    bad = {
        "head_commit_sha": "bad",
        "corpus_digest": "bad",
        "schema_version": SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": "bad",
    }
    rep = derive_per_query_receipt_coverage(
        route_evidence=re, run_header=bad, claim_context=bad, ledger_terminals=terms,
        resolve_receipt=store.resolve, verify_receipt=store.verify,
        content_hash=store.content_hash, receipt_query_digest=store.query_digest,
        corpus_content_digest=_corpus_digest,
    )
    assert rep.coverage_present is False and rep.context_ok is False


def test_missing_header_field_fails_closed():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = {"head_commit_sha": "a" * 40, "corpus_digest": _corpus_digest(re)}  # missing 3 keys
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_corpus_digest_unbound_from_route_evidence_is_false():  # RCO-1-C
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    # header + claim_context AGREE on a corpus_digest that is UNRELATED to the actual route_evidence
    header = _ctx_for(re)
    header["corpus_digest"] = "sha256:" + "d" * 64  # not == _corpus_digest(re)
    rep = _derive(re, terms, store, run_header=header, claim_context=dict(header))
    assert rep.coverage_present is False
    assert rep.context_ok is True and rep.corpus_bound is False
    assert rep.reason == "corpus_digest_unbound_from_route_evidence"


# --- MF-2 no-orphan / chain / empty corpus --------------------------------------------------

def test_orphan_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("sX", "r1")]  # sX not in corpus
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.orphan_terminals == 1


def test_broken_chain_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store, chain_ok=False)
    assert rep.coverage_present is False and rep.reason == "ledger_chain_broken"


def test_empty_corpus_is_false():
    store = _Store()
    rep = _derive([], [], store)
    assert rep.coverage_present is False and rep.reason == "empty_corpus"


# --- claim-safety posture -------------------------------------------------------------------

def test_report_has_no_claim_safe_field_and_is_marked_measurement_only():
    store = _Store()
    store.add_genuine("r1", "qd1")
    rep = _derive([_re("s1", "qd1")], [_receipt_term("s1", "r1")], store)
    assert rep.measurement_marker == "measurement_not_a_correctness_gate"
    assert not any("claim_safe" in f for f in rep._fields)
