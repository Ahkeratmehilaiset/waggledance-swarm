from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from waggledance.core.learning.understanding_contracts import HexCellAddressV1
from waggledance.core.learning.understanding_loop import UnderstandingLoop
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest
from waggledance.core.magma.understanding_ledger import (
    DISPOSITION_RECORDED,
    GENESIS_EVENT_HASH,
    UnderstandingLedger,
    UnderstandingLedgerCorruptionError,
    UnderstandingLedgerError,
    UnderstandingLedgerHeadConflictError,
    UnderstandingLedgerHeadV1,
    UnderstandingLedgerOverflowError,
    validate_understanding_event_payload,
    verify_understanding_event_chain,
)


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _cell() -> HexCellAddressV1:
    return HexCellAddressV1(
        cell_id="cell-ledger",
        q=0,
        r=0,
        incarnation_id="inc-ledger",
        generation=1,
        fence=7,
    )


def _observation(*, value: float = 21.25, source_seq: int = 1) -> dict[str, object]:
    return {
        "observation_id": f"obs-{source_seq}",
        "source": "mqtt",
        "source_seq": source_seq,
        "entity_id": "wd.synthetic.ledger",
        "metric": "temperature",
        "unit": "Cel",
        "value": value,
        "quality": 0.9,
        "privacy_class": "synthetic",
        "observed_at_utc": "2026-08-02T12:00:00Z",
        "metadata": {"fixture": "ledger"},
    }


def _terminal_payload(tag: str = "one") -> dict[str, object]:
    return {
        "ticket_id": sha256_digest({"ticket": tag}),
        "observation_commitment_digest": sha256_digest({"observation": tag}),
        "prediction_digest": None,
        "disposition": "privacy_blocked",
        "reason_codes": ["private_shadow_learning_disabled"],
        "runtime_authority_applied": False,
        "routing_influence_applied": False,
    }


def test_sqlite_configuration_and_loop_sink_round_trip(tmp_path) -> None:
    path = tmp_path / "understanding.db"
    with UnderstandingLedger(path) as ledger:
        assert ledger._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert ledger._conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert ledger._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

        loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
        ticket = loop.prepare_observation(_observation())
        outcome = loop.complete_numeric(ticket, 21.25)

        assert outcome.runtime_authority_applied is False
        assert ledger.event_count == 4
        events = ledger.read_verified_events(expected_head=ledger.head)
        assert [event["event_kind"] for event in events] == [
            "prediction_committed",
            "observation_revealed",
            "disposition_recorded",
            "local_provisional_update",
        ]
        assert events[0]["prev_event_hash"] == GENESIS_EVENT_HASH
        assert events[-1]["event_hash"] == ledger.head
        assert events[0]["payload"]["source_sequence_identity_digest"].startswith(
            "sha256:"
        )
        assert events[0]["payload"]["residual_abs_threshold"] == 2.0
        assert events[0]["payload"]["state_update_alpha"] == 0.1

    with UnderstandingLedger(path) as reopened:
        assert reopened.event_count == 4
        assert reopened.head == events[-1]["event_hash"]


def test_append_batch_is_atomic_and_idempotent(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "batch.db") as ledger:
        batch = ((DISPOSITION_RECORDED, _terminal_payload()),)
        first = ledger.append_batch(batch, idempotency_key="terminal-batch-one")
        second = ledger.append_batch(batch, idempotency_key="terminal-batch-one")
        assert second == first
        assert ledger.event_count == 1

        with pytest.raises(UnderstandingLedgerError, match="payload mismatch"):
            ledger.append_batch(
                ((DISPOSITION_RECORDED, _terminal_payload("different")),),
                idempotency_key="terminal-batch-one",
            )
        assert ledger.event_count == 1

        with pytest.raises(UnderstandingLedgerError, match="event kind"):
            ledger.append_batch(
                (
                    (DISPOSITION_RECORDED, _terminal_payload("valid-first")),
                    ("not_a_v1_event", {}),
                ),
                idempotency_key="must-rollback",
            )
        assert ledger.event_count == 1


def test_compare_and_append_refuses_stale_head_without_writing(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "head-cas.db") as ledger:
        expected_head = ledger.head
        winning = ((DISPOSITION_RECORDED, _terminal_payload("winner")),)
        losing = ((DISPOSITION_RECORDED, _terminal_payload("loser")),)

        ledger.append_batch(winning, idempotency_key="winning-batch")
        winner_head = ledger.head
        with pytest.raises(
            UnderstandingLedgerHeadConflictError,
            match="head changed",
        ):
            ledger.append_batch_if_head(
                losing,
                idempotency_key="losing-batch",
                expected_head=expected_head,
            )

        assert ledger.event_count == 1
        assert ledger.head == winner_head
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM understanding_batches "
            "WHERE idempotency_key='losing-batch'"
        ).fetchone()[0] == 0

        receipt = ledger.append_batch_if_head(
            losing,
            idempotency_key="losing-batch",
            expected_head=winner_head,
        )
        retry = ledger.append_batch_if_head(
            losing,
            idempotency_key="losing-batch",
            expected_head=winner_head,
        )
        assert retry == receipt
        assert ledger.event_count == 2


def test_append_event_appends_each_call_while_batches_are_idempotent(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "single.db") as ledger:
        first = ledger.append_event(DISPOSITION_RECORDED, _terminal_payload("repeat"))
        second = ledger.append_event(DISPOSITION_RECORDED, _terminal_payload("repeat"))
        assert first != second
        assert ledger.event_count == 2
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM understanding_batches"
        ).fetchone()[0] == 2
        assert len(ledger.read_verified_events()) == 2


def test_two_sqlite_writers_serialize_one_idempotent_batch(tmp_path) -> None:
    path = tmp_path / "two-writers.db"
    first = UnderstandingLedger(path)
    second = UnderstandingLedger(path)
    barrier = threading.Barrier(2)
    receipts: list[tuple[str, ...]] = []
    failures: list[BaseException] = []

    def append(ledger: UnderstandingLedger) -> None:
        try:
            barrier.wait(timeout=5)
            receipts.append(
                ledger.append_batch(
                    ((DISPOSITION_RECORDED, _terminal_payload("concurrent")),),
                    idempotency_key="concurrent-batch",
                )
            )
        except BaseException as exc:  # test captures worker failures explicitly
            failures.append(exc)

    threads = [
        threading.Thread(target=append, args=(first,)),
        threading.Thread(target=append, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    try:
        assert failures == []
        assert len(receipts) == 2
        assert receipts[0] == receipts[1]
        assert first.event_count == second.event_count == 1
        assert first.read_verified_events() == second.read_verified_events()
    finally:
        first.close()
        second.close()


def test_payload_allowlist_rejects_authority_extra_and_raw_header_value(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "allowlist.db") as ledger:
        authority = _terminal_payload()
        authority["runtime_authority_applied"] = True
        with pytest.raises(UnderstandingLedgerError, match="exactly false"):
            ledger.append_event(DISPOSITION_RECORDED, authority)

        extra = _terminal_payload()
        extra["raw_prompt"] = "must-not-enter-ledger"
        with pytest.raises(UnderstandingLedgerError, match="keys refused"):
            ledger.append_event(DISPOSITION_RECORDED, extra)
        assert ledger.event_count == 0


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("state_update_alpha", 0.2, "exactly 0.1"),
        ("residual_abs_threshold", 0.0, "must be positive"),
        (
            "source_sequence_identity_digest",
            "hmac-sha256:" + "a" * 64,
            "canonical digest",
        ),
        (
            "learning_domain_digest",
            sha256_digest({"different": "domain"}),
            "learning_domain_digest mismatch",
        ),
    ],
)
def test_prediction_replay_parameters_are_exactly_validated(
    tmp_path, field, replacement, message
) -> None:
    with UnderstandingLedger(tmp_path / f"prediction-{field}.db") as ledger:
        loop = UnderstandingLoop(cell=_cell(), event_sink=ledger, clock=lambda: NOW)
        loop.prepare_observation(_observation())
        payload = copy.deepcopy(ledger.events[0]["payload"])
    payload[field] = replacement
    with pytest.raises(UnderstandingLedgerError, match=message):
        validate_understanding_event_payload("prediction_committed", payload)


def test_payload_json_is_stored_in_canonical_spelling(tmp_path) -> None:
    with UnderstandingLedger(tmp_path / "canonical.db") as ledger:
        payload = _terminal_payload()
        ledger.append_event(DISPOSITION_RECORDED, payload)
        stored = ledger._conn.execute(
            "SELECT payload_json FROM understanding_events WHERE seq=1"
        ).fetchone()[0]
        assert stored == canonical_json_bytes(payload).decode("utf-8")
        assert json.loads(stored) == payload


def test_update_and_delete_triggers_make_both_tables_append_only(tmp_path) -> None:
    path = tmp_path / "triggers.db"
    with UnderstandingLedger(path) as ledger:
        ledger.append_batch(
            ((DISPOSITION_RECORDED, _terminal_payload()),),
            idempotency_key="trigger-batch",
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger._conn.execute(
                "UPDATE understanding_events SET event_kind=event_kind WHERE seq=1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger._conn.execute("DELETE FROM understanding_events WHERE seq=1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger._conn.execute(
                "UPDATE understanding_batches SET event_count=1 "
                "WHERE idempotency_key='trigger-batch'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger._conn.execute(
                "DELETE FROM understanding_batches "
                "WHERE idempotency_key='trigger-batch'"
            )


def test_verified_read_uses_one_event_and_receipt_snapshot(tmp_path, monkeypatch) -> None:
    path = tmp_path / "snapshot.db"
    reader = UnderstandingLedger(path)
    writer = UnderstandingLedger(path)
    reader.append_event(DISPOSITION_RECORDED, _terminal_payload("before"))
    event_rows_selected = threading.Event()
    writer_finished = threading.Event()
    failures: list[BaseException] = []
    original_verify = reader._verify_batch_receipts_locked

    def pause_before_receipt_read(events) -> None:
        event_rows_selected.set()
        if not writer_finished.wait(timeout=10):
            raise AssertionError("concurrent writer did not finish")
        original_verify(events)

    monkeypatch.setattr(reader, "_verify_batch_receipts_locked", pause_before_receipt_read)

    def append_concurrently() -> None:
        try:
            if not event_rows_selected.wait(timeout=10):
                raise AssertionError("reader did not select event rows")
            writer.append_event(DISPOSITION_RECORDED, _terminal_payload("after"))
        except BaseException as exc:
            failures.append(exc)
        finally:
            writer_finished.set()

    thread = threading.Thread(target=append_concurrently)
    thread.start()
    try:
        snapshot = reader.read_verified_events()
        thread.join(timeout=10)
        assert failures == []
        assert len(snapshot) == 1
        assert len(reader.read_verified_events()) == 2
    finally:
        reader.close()
        writer.close()


def test_unversioned_tables_and_modified_trigger_definitions_fail_closed(tmp_path) -> None:
    unversioned = tmp_path / "unversioned.db"
    connection = sqlite3.connect(unversioned)
    connection.execute("CREATE TABLE understanding_events (seq INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(UnderstandingLedgerCorruptionError, match="unversioned"):
        UnderstandingLedger(unversioned)

    modified = tmp_path / "modified-trigger.db"
    ledger = UnderstandingLedger(modified)
    ledger.close()
    connection = sqlite3.connect(modified)
    connection.execute("DROP TRIGGER understanding_events_no_update")
    connection.execute(
        "CREATE TRIGGER understanding_events_no_update "
        "BEFORE UPDATE ON understanding_events BEGIN "
        "SELECT RAISE(ABORT, 'different trigger body'); END"
    )
    connection.commit()
    connection.close()
    with pytest.raises(UnderstandingLedgerCorruptionError, match="definition mismatch"):
        UnderstandingLedger(modified)


def test_deleted_append_event_receipt_is_detected_even_if_trigger_is_restored(
    tmp_path,
) -> None:
    path = tmp_path / "deleted-receipt.db"
    ledger = UnderstandingLedger(path)
    ledger.append_event(DISPOSITION_RECORDED, _terminal_payload("covered"))
    ledger.close()

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER understanding_batches_no_delete")
    connection.execute("DELETE FROM understanding_batches")
    connection.execute(
        "CREATE TRIGGER understanding_batches_no_delete "
        "BEFORE DELETE ON understanding_batches BEGIN "
        "SELECT RAISE(ABORT, 'understanding_batches is append-only'); END"
    )
    connection.commit()
    connection.close()
    with pytest.raises(UnderstandingLedgerCorruptionError, match="receipt coverage"):
        UnderstandingLedger(path)


def test_sequence_overflow_is_a_typed_pre_mutation_error(tmp_path, monkeypatch) -> None:
    ledger = UnderstandingLedger(tmp_path / "overflow.db")
    monkeypatch.setattr(
        ledger,
        "_current_head_locked",
        lambda: UnderstandingLedgerHeadV1((1 << 63) - 1, GENESIS_EVENT_HASH),
    )
    try:
        with pytest.raises(UnderstandingLedgerOverflowError, match="exhausted"):
            ledger.append_event(DISPOSITION_RECORDED, _terminal_payload("overflow"))
        assert ledger._conn.execute(
            "SELECT COUNT(*) FROM understanding_events"
        ).fetchone()[0] == 0
    finally:
        ledger.close()


def test_verified_replay_rejects_tamper_and_wrong_expected_head(tmp_path) -> None:
    path = tmp_path / "tamper.db"
    with UnderstandingLedger(path) as ledger:
        ledger.append_event(DISPOSITION_RECORDED, _terminal_payload())
        events = ledger.events
        with pytest.raises(UnderstandingLedgerCorruptionError, match="expected"):
            ledger.read_verified_events(expected_head=sha256_digest({"wrong": True}))

    tampered = [dict(events[0])]
    tampered[0]["payload"] = dict(tampered[0]["payload"])
    tampered[0]["payload"]["reason_codes"] = ["tampered"]
    with pytest.raises(UnderstandingLedgerCorruptionError, match="self hash"):
        verify_understanding_event_chain(tampered)

    non_integer_sequence = [dict(events[0])]
    non_integer_sequence[0]["seq"] = True
    with pytest.raises(UnderstandingLedgerCorruptionError, match="sequence"):
        verify_understanding_event_chain(non_integer_sequence)

    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER understanding_events_no_update")
        connection.execute(
            "UPDATE understanding_events SET event_hash=? WHERE seq=1",
            (sha256_digest({"forged": True}),),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(UnderstandingLedgerCorruptionError):
        UnderstandingLedger(path)


def test_strict_types_and_closed_ledger_fail_closed(tmp_path) -> None:
    with pytest.raises(UnderstandingLedgerError, match="WAL"):
        UnderstandingLedger(":memory:")
    with pytest.raises(UnderstandingLedgerError, match="busy_timeout"):
        UnderstandingLedger(tmp_path / "bad-timeout.db", busy_timeout_ms=True)

    ledger = UnderstandingLedger(tmp_path / "closed.db")
    ledger.close()
    with pytest.raises(UnderstandingLedgerError, match="closed"):
        ledger.append_event(DISPOSITION_RECORDED, _terminal_payload())
