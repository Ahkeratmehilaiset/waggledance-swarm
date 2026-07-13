"""Adversarial matrix for S2 per-query receipt coverage (frozen W1A v3 + rco-1 MF-1a).

Each forgery/omission case MUST return coverage_present=False; the happy path MUST return
True (liveness, so no guard is vacuously failing). Fake resolver seams let us inject
nonexistent / content-mismatched / verify-failing / cross-query receipts directly.
"""

from waggledance.core.magma.chat_served_per_query_coverage import (
    MEASUREMENT_MARKER,
    NORMALIZATION_VERSION,
    SCHEMA_VERSION,
    derive_per_query_receipt_coverage,
)

CTX = {
    "head_commit_sha": "a" * 40,
    "corpus_digest": "sha256:" + "c" * 64,
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
        self._r = {}  # ref -> receipt object

    def add_genuine(self, ref, query_digest):
        self._r[ref] = {"_content_hash": ref, "_verify_ok": True, "_query_digest": query_digest}
        return ref

    def add_forged(self, ref, query_digest, *, content_hash=None, verify_ok=True):
        # a persisted receipt that may lie about its content hash or fail verification
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


def _derive(route_evidence, terminals, store, *, run_header=None, pending_append_failures=0, chain_ok=True):
    return derive_per_query_receipt_coverage(
        route_evidence=route_evidence,
        run_header=run_header if run_header is not None else dict(CTX),
        claim_context=CTX,
        ledger_terminals=terminals,
        resolve_receipt=store.resolve,
        verify_receipt=store.verify,
        content_hash=store.content_hash,
        receipt_query_digest=store.query_digest,
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
    assert rep.schema_version == SCHEMA_VERSION and rep.measurement_marker == MEASUREMENT_MARKER


# --- rco-2 R1: bijection (duplicate cannot mask a missing) -----------------------------------

def test_duplicate_masks_missing_is_false():
    # rco-2's keystone case: a duplicate bound entry must NOT mask a missing query. Now caught
    # by the per-query one-terminal rule (even earlier than the bijection). Either way: NOT present.
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd2")
    re = [_re("s1", "qd1"), _re("s1b", "qd1"), _re("s2", "qd2"), _re("s3", "qd3")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s1b", "r1"), _receipt_term("s2", "r2")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False       # the duplicate cannot mask the missing qd3
    assert rep.duplicate_query_terminal >= 1   # qd1 has two terminals -> detected


def test_missing_query_bijection_unmet_is_false():
    # pure bijection: distinct queries, one with NO terminal at all, NO duplicate -> not present.
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd2")
    re = [_re("s1", "qd1"), _re("s2", "qd2"), _re("s3", "qd3")]
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]  # qd3 unbound
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert "bijection_unmet" in rep.reason
    assert rep.missing == 1  # qd3


# --- rco-1 MF-1 / MF-1a: forgery keystone ---------------------------------------------------

def test_nonexistent_receipt_is_false():
    store = _Store()  # ref never added -> resolve returns None
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "sha256:" + "9" * 64)]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_cross_query_receipt_is_false():
    store = _Store()
    store.add_genuine("r1", "qdX")  # receipt bound to a DIFFERENT query
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_content_mismatch_is_false():
    store = _Store()
    # present + verify_ok + right query, but content_hash != receipt_ref (not content-addressed)
    store.add_forged("r1", "qd1", content_hash="sha256:" + "f" * 64)
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


def test_forged_persisted_verify_fail_is_false():
    store = _Store()
    # content-addressed + right query, but FAILS verify_magma_receipt
    store.add_forged("r1", "qd1", verify_ok=False)
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.forged_or_unbound == 1


# --- rco-1 MF-2: corpus denominator + fail-open durability ----------------------------------

def test_corpus_missing_entry_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1"), _re("s2", "qd2")]  # qd2 has no terminal
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


# --- rco-1 MF-3: one terminal per served id; gap stays gap -----------------------------------

def test_gap_then_bound_duplicate_terminal_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_gap_term("s1"), _receipt_term("s1", "r1")]  # a later bound must NOT paper the gap
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert rep.duplicate_terminal == 1


def test_plain_gap_is_false():
    store = _Store()
    re = [_re("s1", "qd1")]
    terms = [_gap_term("s1")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False and rep.gapped == 1


# --- rco-2 R2: stale-head / wrong-corpus binding --------------------------------------------

def test_stale_head_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = dict(CTX)
    header["head_commit_sha"] = "b" * 40  # measured under a different head
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False
    assert rep.reason == "stale_or_wrong_measurement_context"


def test_wrong_corpus_digest_is_false():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = dict(CTX)
    header["corpus_digest"] = "sha256:" + "d" * 64
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_missing_header_field_fails_closed():
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = {"head_commit_sha": "a" * 40, "corpus_digest": "sha256:" + "c" * 64}  # no schema_version
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_wrong_normalization_version_is_false():  # tools W1C probe 1
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = dict(CTX)
    header["normalization_version"] = "wd.chat_query_normalization.vX"
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_wrong_run_id_is_false():  # tools W1C probe 1
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    header = dict(CTX)
    header["run_id"] = "sha256:" + "9" * 64
    rep = _derive(re, terms, store, run_header=header)
    assert rep.coverage_present is False and rep.context_ok is False


def test_malformed_but_equal_header_is_false():  # tools W1C probe 2
    store = _Store()
    store.add_genuine("r1", "qd1")
    re = [_re("s1", "qd1")]
    terms = [_receipt_term("s1", "r1")]
    bad = {
        "head_commit_sha": "bad",             # not full-40 hex
        "corpus_digest": "bad",               # not sha256
        "schema_version": SCHEMA_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "run_id": "bad",                       # not sha256
    }
    # both run_header AND claim_context are the same malformed dict -> equality alone would pass
    rep = derive_per_query_receipt_coverage(
        route_evidence=re, run_header=bad, claim_context=bad, ledger_terminals=terms,
        resolve_receipt=store.resolve, verify_receipt=store.verify,
        content_hash=store.content_hash, receipt_query_digest=store.query_digest,
    )
    assert rep.coverage_present is False and rep.context_ok is False


def test_two_terminals_one_query_is_false():  # tools W1C probe 3 (MF-3 at query scope)
    store = _Store()
    store.add_genuine("r1", "qd1")
    store.add_genuine("r2", "qd1")  # a second genuine receipt for the SAME query
    re = [_re("s1", "qd1"), _re("s2", "qd1")]  # two served events, same query
    terms = [_receipt_term("s1", "r1"), _receipt_term("s2", "r2")]
    rep = _derive(re, terms, store)
    assert rep.coverage_present is False
    assert rep.duplicate_query_terminal == 1
    assert "duplicate_terminal_per_query" in rep.reason


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


# --- claim-safety posture: coverage never carries claim_safe --------------------------------

def test_report_has_no_claim_safe_field_and_is_marked_measurement_only():
    store = _Store()
    store.add_genuine("r1", "qd1")
    rep = _derive([_re("s1", "qd1")], [_receipt_term("s1", "r1")], store)
    assert rep.measurement_marker == "measurement_not_a_correctness_gate"
    assert not any("claim_safe" in f for f in rep._fields)
