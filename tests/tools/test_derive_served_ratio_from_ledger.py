# SPDX-License-Identifier: BUSL-1.1
"""Durable-ledger served-ratio derivation (readiness blocker B3).

Locks ``tools/derive_served_ratio_from_ledger.py`` to the exact
``RouteTelemetry.solver_first_served_stats`` semantics (numerator counted only
within the served denominator; empty denominator -> 0.0) while sourcing every
count from the hash-chained chat-served ledger, and locks the honesty
boundaries: chain corruption, a missing ledger file, and a second terminal
for one served_id are structured rejections, unresolved pendings and gaps are
always visible next to the ratio, and no ``claim_safe`` field is ever
emitted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.derive_served_ratio_from_ledger import (
    EXIT_OK,
    EXIT_REJECTED,
    DerivationRejected,
    compare_with_telemetry,
    derive_report,
    main,
)
from waggledance.core.magma.chat_served_ledger import (
    GENESIS_PREV_HASH,
    append_entry,
    new_gap_terminal,
    new_receipt_terminal,
    new_served_pending,
)

RECEIPT_REF = "sha256:" + "a" * 64


class LedgerBuilder:
    """Append well-formed chained entries to a ledger fixture."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.prev = GENESIS_PREV_HASH

    def _append(self, entry: dict) -> None:
        append_entry(str(self.path), entry, fsync=False)
        self.prev = entry["entry_hash"]

    def served(
        self,
        served_id: str,
        *,
        route_type: str | None = "solver",
        ts: str = "2026-08-21T09:00:00Z",
    ) -> None:
        metadata = {"source": "chat"}
        if route_type is not None:
            metadata["route_type"] = route_type
        self._append(new_served_pending(served_id, self.prev, ts, metadata))

    def receipt(self, served_id: str, *, ts: str = "2026-08-21T09:30:00Z") -> None:
        self._append(new_receipt_terminal(served_id, self.prev, ts, RECEIPT_REF))

    def gap(self, served_id: str, *, ts: str = "2026-08-21T09:30:00Z") -> None:
        self._append(new_gap_terminal(served_id, self.prev, ts, "sink_write_failed"))


@pytest.fixture
def ledger(tmp_path: Path) -> LedgerBuilder:
    return LedgerBuilder(tmp_path / "ledger.jsonl")


def test_empty_present_ledger_yields_zero_ratio(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"")
    report = derive_report(ledger_path=str(path))
    assert report["served_total"] == 0
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] == 0.0
    assert report["receipt_coverage"] == {"receipted": 0, "gap": 0, "pending": 0}


def test_missing_ledger_rejects_not_zero_report(tmp_path: Path) -> None:
    # rco-2 2026-08-21T20:43:37Z finding (tools-confirmed 20:46:55Z): a
    # nonexistent --ledger path used to produce the SAME complete zero report
    # as a real empty ledger, so a path misconfiguration read as a
    # confirmed-complete measurement of zero servings. An absent file is no
    # evidence, not clean evidence.
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(ledger_path=str(tmp_path / "missing.jsonl"))
    assert excinfo.value.reason == "ledger_not_found"


def test_basic_ratio_from_receipted_entries(ledger: LedgerBuilder) -> None:
    for index in range(3):
        ledger.served(f"sol-{index}", route_type="solver")
        ledger.receipt(f"sol-{index}")
    for index in range(2):
        ledger.served(f"llm-{index}", route_type="llm")
        ledger.receipt(f"llm-{index}")

    report = derive_report(ledger_path=str(ledger.path))

    assert report["served_total"] == 5
    assert report["solver_first_served_total"] == 3
    assert report["solver_first_served_ratio"] == pytest.approx(3 / 5)
    assert report["per_route_served"] == {"llm": 2, "solver": 3}
    assert report["receipt_coverage"] == {"receipted": 5, "gap": 0, "pending": 0}
    assert report["gapless"] is True
    assert report["evidence_complete"] is True


def test_subset_invariant_when_denominator_excludes_solver(
    ledger: LedgerBuilder,
) -> None:
    # Mirrors RouteTelemetry: narrowing the denominator to exclude the solver
    # route must drop those serves from BOTH numerator and denominator, so the
    # ratio stays <= 1.0 instead of inflating.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.served("llm-0", route_type="llm")
    ledger.receipt("llm-0")

    report = derive_report(
        ledger_path=str(ledger.path),
        served_route_types=("llm",),
    )

    assert report["served_total"] == 1
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] == 0.0


def test_unresolved_pending_visible_and_not_complete(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.served("sol-1", route_type="solver")

    report = derive_report(ledger_path=str(ledger.path))

    assert report["receipt_coverage"] == {"receipted": 1, "gap": 0, "pending": 1}
    assert report["evidence_complete"] is False
    assert report["solver_first_served_ratio"] == pytest.approx(1.0)


def test_gap_terminal_breaks_gapless_flag(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.gap("sol-0")

    report = derive_report(ledger_path=str(ledger.path))

    assert report["receipt_coverage"] == {"receipted": 0, "gap": 1, "pending": 0}
    assert report["gapless"] is False
    assert report["evidence_complete"] is True


def test_conflicting_second_terminal_rejects(ledger: LedgerBuilder) -> None:
    # rco-2 2026-08-21T20:39:59Z finding: verify_chain checks hash linkage
    # only, so a chain-VALID ledger can carry receipt+gap terminals for one
    # served_id; the old silent first-terminal-wins kept "receipted" and
    # reported gapless=True / evidence_complete=True while a gap terminal
    # genuinely existed. Any second terminal now rejects, mirroring
    # chat_served_accounting's "second_terminal" lifecycle rule.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.gap("sol-0")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(ledger_path=str(ledger.path))
    assert excinfo.value.reason == "second_terminal_for_served_id:sol-0"


def test_duplicate_same_type_terminal_also_rejects(ledger: LedgerBuilder) -> None:
    # The accounting layer counts ANY second terminal as a lifecycle
    # violation, not only a conflicting-type one; mirror that exactly.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.receipt("sol-0")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(ledger_path=str(ledger.path))
    assert excinfo.value.reason == "second_terminal_for_served_id:sol-0"


def test_chain_tamper_is_rejected(ledger: LedgerBuilder, tmp_path: Path) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["metadata"]["route_type"] = "llm"  # content change without re-hash
    lines[0] = json.dumps(first, sort_keys=True)
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(ledger_path=str(ledger.path))
    assert excinfo.value.reason.startswith("chain_invalid:")


def test_torn_tail_tolerated_and_reported(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with ledger.path.open("ab") as handle:
        handle.write(b'{"torn": tr')  # crash-shaped unparseable FINAL line

    report = derive_report(ledger_path=str(ledger.path))

    assert report["torn_tail"] is True
    assert report["evidence_complete"] is False
    assert report["served_total"] == 1


def test_window_bounds_filter_serves_but_not_coverage(ledger: LedgerBuilder) -> None:
    ledger.served("early", route_type="solver", ts="2026-08-21T08:00:00Z")
    ledger.served("in-window", route_type="solver", ts="2026-08-21T09:00:00Z")
    # Terminal falls OUTSIDE the window: coverage still binds by served_id.
    ledger.receipt("in-window", ts="2026-08-21T11:00:00Z")
    ledger.served("late", route_type="llm", ts="2026-08-21T10:30:00Z")

    report = derive_report(
        ledger_path=str(ledger.path),
        window_start="2026-08-21T08:30:00Z",
        window_end="2026-08-21T10:00:00Z",
    )

    assert report["served_total"] == 1
    assert report["per_route_served"] == {"solver": 1}
    assert report["receipt_coverage"] == {"receipted": 1, "gap": 0, "pending": 0}
    assert report["window"]["windowed"] is True
    assert report["window"]["out_of_window_served_entries"] == 2
    assert report["window"]["named_production_window"] is False


def test_window_uses_parsed_time_not_string_order(ledger: LedgerBuilder) -> None:
    # '.' < 'Z' lexicographically, so raw string comparison would place the
    # sub-second timestamp BEFORE the window start; parsed comparison keeps it in.
    ledger.served("subsec", route_type="solver", ts="2026-08-21T09:00:00.500Z")
    ledger.receipt("subsec")

    report = derive_report(
        ledger_path=str(ledger.path),
        window_start="2026-08-21T09:00:00Z",
        window_end="2026-08-21T09:01:00Z",
    )

    assert report["served_total"] == 1


def test_unknown_route_counts_in_default_denominator_never_numerator(
    ledger: LedgerBuilder,
) -> None:
    ledger.served("mystery", route_type=None)
    ledger.receipt("mystery")
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    report = derive_report(ledger_path=str(ledger.path))

    assert report["served_total"] == 2
    assert report["solver_first_served_total"] == 1
    assert report["per_route_served"] == {"solver": 1, "unknown": 1}

    narrowed = derive_report(
        ledger_path=str(ledger.path),
        served_route_types=("solver",),
    )
    assert narrowed["served_total"] == 1
    assert narrowed["excluded_unknown_route_count"] == 1


def test_unknown_never_counts_as_solver_even_when_explicitly_configured(
    ledger: LedgerBuilder,
) -> None:
    # rco-1 2026-08-21T21:24:49Z finding (rco-2 spot-confirmed): under an
    # explicit --solver-route-type unknown override, an entry with MISSING
    # route_type metadata counted toward solver_first_served_total (1/1),
    # falsifying the docstring's "UNKNOWN_ROUTE_TYPE can never satisfy solver
    # membership" invariant. The exclusion is now unconditional at the count
    # site, mirroring the served-side unknown guard.
    ledger.served("mystery", route_type=None)
    ledger.receipt("mystery")

    report = derive_report(
        ledger_path=str(ledger.path),
        solver_route_types=("unknown",),
    )
    assert report["served_total"] == 1
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] == 0.0


def test_unparseable_entry_ts_under_window_rejects(ledger: LedgerBuilder) -> None:
    ledger.served("odd-ts", route_type="solver", ts="not-a-time")

    derive_report(ledger_path=str(ledger.path))  # windowless: fine

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            window_start="2026-08-21T08:00:00Z",
        )
    assert excinfo.value.reason == "entry_ts_unparseable_under_window"


def test_naive_entry_ts_under_window_rejects_not_typeerror(
    ledger: LedgerBuilder,
) -> None:
    # tools 2026-08-21T10:56:25Z finding: a hash-valid offset-NAIVE ts_utc
    # under an aware window bound raised an uncaught TypeError at the
    # comparison instead of the promised structured rejection.
    ledger.served("naive", route_type="solver", ts="2026-08-21T09:00:00")

    derive_report(ledger_path=str(ledger.path))  # windowless: unaffected

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            window_start="2026-08-21T08:00:00Z",
        )
    assert excinfo.value.reason == "entry_ts_unparseable_under_window"


def test_naive_window_bound_rejects_as_unparseable(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver", ts="2026-08-21T09:00:00Z")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            window_start="2026-08-21T08:00:00",
        )
    assert excinfo.value.reason == "window_start_unparseable"

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            window_end="2026-08-21T10:00:00",
        )
    assert excinfo.value.reason == "window_end_unparseable"


def test_telemetry_divergence_reported_without_preference() -> None:
    report = {"solver_first_served_ratio": 0.6}
    same = compare_with_telemetry(report, {"solver_first_served_ratio": 0.6})
    assert same["diverges"] is False
    different = compare_with_telemetry(report, {"solver_first_served_ratio": 0.95})
    assert different["diverges"] is True
    assert different["abs_delta"] == pytest.approx(0.35)
    with pytest.raises(DerivationRejected):
        compare_with_telemetry(report, {"solver_first_served_ratio": True})


def test_report_never_contains_claim_safe(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    report = derive_report(ledger_path=str(ledger.path))

    assert "claim_safe" not in json.dumps(report)
    assert report["flags"]["measurement_only"] is True
    assert report["flags"]["runtime_authority_granted"] is False


def test_cli_json_and_exit_codes(
    ledger: LedgerBuilder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    assert main(["--ledger", str(ledger.path), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["derived"] is True
    assert payload["schema"] == "wd.served_ratio_from_ledger.v1"
    assert payload["solver_first_served_ratio"] == pytest.approx(1.0)

    snapshot = tmp_path / "telemetry.json"
    snapshot.write_text(
        json.dumps({"solver_first_served_ratio": 1.0}), encoding="utf-8"
    )
    assert (
        main(
            [
                "--ledger",
                str(ledger.path),
                "--telemetry-snapshot",
                str(snapshot),
                "--json",
            ]
        )
        == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["telemetry_divergence"]["diverges"] is False

    assert (
        main(["--ledger", str(ledger.path), "--window-start-ts", "garbage"])
        == EXIT_REJECTED
    )
    rejection = json.loads(capsys.readouterr().out)
    assert rejection["derived"] is False
    assert rejection["reason"] == "window_start_unparseable"
