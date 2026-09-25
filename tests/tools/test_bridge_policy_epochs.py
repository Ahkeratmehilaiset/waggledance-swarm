# SPDX-License-Identifier: BUSL-1.1
"""Tests for the evidence-derived epoch snapshot builder.

The interesting properties here are negative ones -- that nothing is invented,
nothing is defaulted, and no clock or random source can leak in -- so most of
this file is about what the builder *refuses* to produce. The final section
checks the join with admission, because a snapshot that cannot be consumed
safely would be worse than no snapshot at all.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bridge_capacity_advisor import InputError  # noqa: E402
from tools.bridge_policy_epochs import (  # noqa: E402
    EPOCH_SNAPSHOT_SCHEMA,
    MAX_EVIDENCE_BYTES,
    epoch_inputs,
    snapshot,
    to_admission_epochs,
)
from tools.bridge_task_admission import EPOCH_FIELDS, KEEP, PARK, admit  # noqa: E402

H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
H4 = "d" * 64

BINDING = {
    "agent_id": "fable-5",
    "session_id": "wd-fable-direct-20260924T090104Z",
    "native_thread_id": "9f375967-f824-4e2e-8104-7f0011117cf5",
    "task_id": "codex-lead-1/autonomy-epochs-20260925",
    "request_id": "fec1701e-fb65-4c48-8eb1-940a726d0017",
    "head": "1ab68426f6bd5b59931227b733e55eeb23c3cd34",
    "claim_id": "fable-5/autonomy-epochs-20260925",
    "scope_digest": H1,
    "authority_ref": "operator/finish-implementation",
    "policy_digest": H2,
    "permission_digest": H3,
    "native_pid": 24400,
    "native_process_started_at": "2026-09-24T09:01:04.252477+00:00",
}


def evidence():
    return {
        "policy": {"documents": [{"ref": "docs/POLICY.md", "sha256": H1},
                                 {"ref": "configs/policy.json", "sha256": H2}]},
        "catalog": {"ref": "configs/profile_catalog.json", "sha256": H3},
        "qualification": {
            "ref": "reports/qualification.json", "sha256": H4,
            "evidence_ids": ["ev-1", "ev-2"],
            "verdicts": {"fable-producer/default": "qualified"},
        },
        "profile": {"ref": "configs/profiles/fable.json", "sha256": H1,
                    "profile_id": "fable-producer/default",
                    "authorization_ref": "operator/2026-09-25"},
    }


def complete_snapshot():
    return snapshot(evidence(), binding=BINDING)


# --- nothing invented, nothing defaulted --------------------------------------


def test_complete_evidence_derives_all_five_epochs():
    snap = complete_snapshot()
    assert set(snap["epochs"]) == set(EPOCH_FIELDS)
    assert snap["unknown"] == {}
    assert snap["complete"] is True
    assert snap["schema"] == EPOCH_SNAPSHOT_SCHEMA


def test_snapshot_grants_nothing_and_authenticates_nothing():
    for snap in (complete_snapshot(), snapshot({}, binding={})):
        assert snap["execution_allowed"] is False
        assert snap["switch_permitted"] is False
        assert snap["provider_authenticated"] is False
        assert snap["quota_verified"] is False


def test_empty_evidence_yields_all_unknown_and_no_epochs():
    snap = snapshot({}, binding={})
    assert snap["epochs"] == {}
    assert set(snap["unknown"]) == set(EPOCH_FIELDS)
    assert snap["complete"] is False
    assert all(reasons for reasons in snap["unknown"].values())


@pytest.mark.parametrize("drop", ["policy", "catalog", "qualification", "profile"])
def test_a_missing_evidence_section_is_unknown_not_defaulted(drop):
    payload = evidence()
    del payload[drop]
    name = f"{drop}_epoch"
    snap = snapshot(payload, binding=BINDING)
    assert name not in snap["epochs"], "an absent section must not produce a value"
    assert name in snap["unknown"]
    assert snap["complete"] is False
    # the other four are unaffected
    assert set(snap["epochs"]) == set(EPOCH_FIELDS) - {name}


# --- determinism, and the absence of a clock or a random source ---------------


def test_the_same_evidence_always_gives_the_same_snapshot():
    assert complete_snapshot() == complete_snapshot()


def test_document_order_does_not_change_the_policy_epoch():
    payload = evidence()
    reversed_docs = deepcopy(payload)
    reversed_docs["policy"]["documents"].reverse()
    assert (snapshot(payload, binding=BINDING)["epochs"]["policy_epoch"]
            == snapshot(reversed_docs, binding=BINDING)["epochs"]["policy_epoch"])


def test_module_imports_no_clock_and_no_random_source():
    """Structural guard: an epoch derived from a clock would not be evidence."""
    source = (ROOT / "tools" / "bridge_policy_epochs.py").read_text(encoding="utf-8")
    for banned in ("import time", "import random", "import uuid", "import secrets",
                   "from datetime", "import datetime", "datetime.now", "time.time",
                   "utcnow", "uuid4"):
        assert banned not in source, f"{banned!r} must not appear in the epoch builder"


@pytest.mark.parametrize("mutate", [
    lambda e, b: e["policy"]["documents"][0].update(sha256=H4),
    lambda e, b: e["policy"]["documents"].append({"ref": "docs/EXTRA.md", "sha256": H4}),
    lambda e, b: e["catalog"].update(sha256=H4),
    lambda e, b: e["catalog"].update(ref="configs/other.json"),
    lambda e, b: e["qualification"]["evidence_ids"].append("ev-3"),
    lambda e, b: e["qualification"]["verdicts"].update({"other": "qualified"}),
    lambda e, b: e["profile"].update(authorization_ref="operator/2026-09-26"),
    lambda e, b: b.update(session_id="wd-fable-other"),
    lambda e, b: b.update(native_pid=999),
    lambda e, b: b.update(native_process_started_at="2026-09-24T10:00:00+00:00"),
])
def test_any_evidence_change_changes_its_epoch(mutate):
    base = complete_snapshot()
    payload, binding = evidence(), deepcopy(BINDING)
    mutate(payload, binding)
    changed = snapshot(payload, binding=binding)
    assert changed["epochs"] != base["epochs"]


def test_epochs_are_namespaced_so_equal_material_cannot_collide():
    values = complete_snapshot()["epochs"]
    assert len(set(values.values())) == len(values)
    for name, value in values.items():
        assert value.startswith(f"{name}:")


# --- malformed evidence is refused, never coerced -----------------------------


@pytest.mark.parametrize("bad", ["", "zz" * 32, "a" * 63, "a" * 65, None, 5, True,
                                 " " + "a" * 64, "a" * 64 + "\n"])
def test_a_bad_digest_makes_its_epoch_unknown(bad):
    payload = evidence()
    payload["catalog"]["sha256"] = bad
    snap = snapshot(payload, binding=BINDING)
    assert "catalog_epoch" not in snap["epochs"]
    assert "catalog_ref_or_digest_malformed" in snap["unknown"]["catalog_epoch"]


def test_uppercase_and_lowercase_digests_agree():
    lower, upper = evidence(), evidence()
    upper["catalog"]["sha256"] = upper["catalog"]["sha256"].upper()
    assert (snapshot(lower, binding=BINDING)["epochs"]["catalog_epoch"]
            == snapshot(upper, binding=BINDING)["epochs"]["catalog_epoch"])


def test_duplicate_policy_refs_are_refused():
    payload = evidence()
    payload["policy"]["documents"] = [{"ref": "docs/POLICY.md", "sha256": H1},
                                      {"ref": "docs/POLICY.md", "sha256": H2}]
    snap = snapshot(payload, binding=BINDING)
    assert "policy_epoch" not in snap["epochs"]
    assert "policy_documents_contain_duplicate_refs" in snap["unknown"]["policy_epoch"]


@pytest.mark.parametrize("documents", [[], "docs", {"ref": "x"}, None])
def test_policy_documents_must_be_a_nonempty_list(documents):
    payload = evidence()
    payload["policy"]["documents"] = documents
    snap = snapshot(payload, binding=BINDING)
    assert "policy_epoch" not in snap["epochs"]


# --- a model label authenticates nothing --------------------------------------


def test_a_model_label_alone_is_not_qualification_evidence():
    """The named failure mode: a label is not an attestation."""
    payload = evidence()
    payload["qualification"] = {"model": "some-model-name"}
    snap = snapshot(payload, binding=BINDING)
    assert "qualification_epoch" not in snap["epochs"]
    reasons = snap["unknown"]["qualification_epoch"]
    assert "qualification_report_ref_or_digest_malformed" in reasons
    assert "qualification_evidence_ids_missing" in reasons
    assert "qualification_verdicts_missing" in reasons


@pytest.mark.parametrize("field,reason", [
    ("evidence_ids", "qualification_evidence_ids_missing"),
    ("verdicts", "qualification_verdicts_missing"),
])
def test_qualification_needs_both_ids_and_verdicts(field, reason):
    payload = evidence()
    del payload["qualification"][field]
    snap = snapshot(payload, binding=BINDING)
    assert "qualification_epoch" not in snap["epochs"]
    assert reason in snap["unknown"]["qualification_epoch"]


def test_duplicate_qualification_evidence_ids_are_refused():
    payload = evidence()
    payload["qualification"]["evidence_ids"] = ["ev-1", "ev-1"]
    snap = snapshot(payload, binding=BINDING)
    assert "qualification_epoch" not in snap["epochs"]


# --- native identity comes from the binding, not from a clock -----------------


@pytest.mark.parametrize("field", ["agent_id", "session_id", "native_thread_id"])
def test_missing_native_identity_field_is_unknown(field):
    binding = deepcopy(BINDING)
    del binding[field]
    snap = snapshot(evidence(), binding=binding)
    assert "native_epoch" not in snap["epochs"]
    assert f"native_identity_missing:{field}" in snap["unknown"]["native_epoch"]


def test_a_reusable_pid_without_a_start_epoch_is_not_an_identity():
    binding = deepcopy(BINDING)
    del binding["native_process_started_at"]
    snap = snapshot(evidence(), binding=binding)
    assert "native_epoch" not in snap["epochs"]
    assert "native_identity_missing:process_epoch" in snap["unknown"]["native_epoch"]


# --- structurally malformed input ---------------------------------------------


@pytest.mark.parametrize("payload", [None, "evidence", 5, []])
def test_malformed_evidence_or_binding_raises(payload):
    with pytest.raises(InputError):
        snapshot(payload, binding=BINDING)
    with pytest.raises(InputError):
        snapshot(evidence(), binding=payload)


def test_unserialisable_evidence_fails_closed():
    payload = evidence()
    payload["extra"] = {1, 2}
    with pytest.raises(InputError):
        snapshot(payload, binding=BINDING)


def test_oversized_evidence_is_refused():
    payload = evidence()
    payload["padding"] = "x" * MAX_EVIDENCE_BYTES
    with pytest.raises(InputError):
        snapshot(payload, binding=BINDING)


def test_epoch_inputs_documents_every_epoch():
    assert set(epoch_inputs()) == set(EPOCH_FIELDS)


# --- the join with admission ---------------------------------------------------


def admission_request(epochs):
    return {
        "binding": deepcopy(BINDING),
        "epochs": epochs,
        "hold": False,
        "cancelled": False,
        "owner": {"verified": True, "principal": "fable-5",
                  "verification_ref": "claim/fable-5/autonomy-epochs-20260925"},
        "current_profile": {"profile_id": "fable-producer/default", "authorized": True,
                            "healthy": True, "authorization_ref": "operator/2026-09-25"},
        "failure": None,
    }


def test_a_complete_snapshot_feeds_admission_and_keeps():
    epochs = to_admission_epochs(complete_snapshot())
    assert set(epochs) == set(EPOCH_FIELDS)
    assert admit(admission_request(epochs))["verdict"] == KEEP


@pytest.mark.parametrize("drop", ["policy", "catalog", "qualification", "profile"])
def test_an_incomplete_snapshot_parks_admission_by_construction(drop):
    """The safety join: unknown epochs are omitted, so admission parks itself."""
    payload = evidence()
    del payload[drop]
    epochs = to_admission_epochs(snapshot(payload, binding=BINDING))
    assert f"{drop}_epoch" not in epochs
    result = admit(admission_request(epochs))
    assert result["verdict"] == PARK
    assert f"epoch_unknown:{drop}_epoch" in result["reasons"]


def test_to_admission_epochs_rejects_a_foreign_record():
    with pytest.raises(InputError):
        to_admission_epochs({"schema": "something.else", "epochs": {}})
    with pytest.raises(InputError):
        to_admission_epochs(None)


def test_to_admission_epochs_never_emits_a_blank_value():
    snap = complete_snapshot()
    snap["epochs"]["catalog_epoch"] = "   "
    assert "catalog_epoch" not in to_admission_epochs(snap)


def test_derived_from_records_the_evidence_actually_used():
    snap = complete_snapshot()
    assert set(snap["derived_from"]) == set(EPOCH_FIELDS)
    assert snap["derived_from"]["catalog_epoch"]["sha256"] == H3
    assert re.match(r"^catalog_epoch:[0-9a-f]{32}$", snap["epochs"]["catalog_epoch"])
