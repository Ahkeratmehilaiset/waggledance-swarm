# SPDX-License-Identifier: BUSL-1.1
"""Durable-ledger served-ratio derivation (readiness blocker B3).

Locks ``tools/derive_served_ratio_from_ledger.py`` to the exact
``RouteTelemetry.solver_first_served_stats`` semantics (numerator counted only
within the served denominator; empty denominator -> 0.0) while sourcing every
count from the hash-chained chat-served ledger, and locks the honesty
boundaries: chain corruption, a missing ledger file, and any per-served_id
lifecycle violation (duplicate pending, terminal without pending, second
terminal) are structured rejections, unresolved pendings and gaps are always
visible next to the ratio, and no ``claim_safe`` field is ever emitted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.derive_served_ratio_from_ledger import (
    EXIT_OK,
    EXIT_REJECTED,
    LIFECYCLE_GAP,
    LIFECYCLE_PENDING,
    LIFECYCLE_RECEIPTED,
    DerivationRejected,
    compare_with_telemetry,
    derive_report,
    lifecycle_violation,
    main,
)
from waggledance.core.magma import chat_served_accounting
from waggledance.core.magma.chat_served_ledger import (
    GAP_TERMINAL,
    GENESIS_PREV_HASH,
    RECEIPT_TERMINAL,
    SERVED_PENDING,
    append_entry,
    compute_entry_hash,
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
        # Explicit empty failure ledger: pending_failure_ledger_path is
        # required, and an empty-but-present regular file asserts zero
        # durable pending-append failures.
        self.failures = path.parent / "pending_failures.jsonl"
        self.failures.write_text("", encoding="utf-8")

    def fail_pending_append(self, lines: int = 1, text: str = "{}") -> None:
        with open(self.failures, "a", encoding="utf-8") as handle:
            for _ in range(lines):
                handle.write(text + "\n")

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
    failures = tmp_path / "pending_failures.jsonl"
    failures.write_text("", encoding="utf-8")
    report = derive_report(
        ledger_path=str(path),
        pending_failure_ledger_path=str(failures),
    )
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
    failures = tmp_path / "pending_failures.jsonl"
    failures.write_text("", encoding="utf-8")
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(tmp_path / "missing.jsonl"),
            pending_failure_ledger_path=str(failures),
        )
    assert excinfo.value.reason == "ledger_not_found"


def test_basic_ratio_from_receipted_entries(ledger: LedgerBuilder) -> None:
    for index in range(3):
        ledger.served(f"sol-{index}", route_type="solver")
        ledger.receipt(f"sol-{index}")
    for index in range(2):
        ledger.served(f"llm-{index}", route_type="llm")
        ledger.receipt(f"llm-{index}")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

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
        pending_failure_ledger_path=str(ledger.failures),
        served_route_types=("llm",),
    )

    assert report["served_total"] == 1
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] == 0.0


def test_unresolved_pending_visible_and_not_complete(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.served("sol-1", route_type="solver")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["receipt_coverage"] == {"receipted": 1, "gap": 0, "pending": 1}
    assert report["evidence_complete"] is False
    assert report["solver_first_served_ratio"] == pytest.approx(1.0)


def test_gap_terminal_breaks_gapless_flag(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.gap("sol-0")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

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
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "second_terminal_for_served_id:sol-0"


def test_duplicate_same_type_terminal_also_rejects(ledger: LedgerBuilder) -> None:
    # The accounting layer counts ANY second terminal as a lifecycle
    # violation, not only a conflicting-type one; mirror that exactly.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.receipt("sol-0")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "second_terminal_for_served_id:sol-0"


def test_duplicate_pending_for_one_served_id_rejects(ledger: LedgerBuilder) -> None:
    # codex-lead-1 2026-08-22T04:30:52Z finding, PoC case 1 (exact shape):
    # two chain-valid pendings for ONE served_id (solver, then llm) plus one
    # receipt used to count served_total=2 / receipted=2 from one logical
    # serve and one terminal, move the ratio to 0.5, and still report
    # evidence_complete=True. The canonical denominator is DISTINCT
    # served_ids; chat_served_accounting rejects this as duplicate_pending.
    ledger.served("same-served-id", route_type="solver", ts="2026-08-21T09:00:00Z")
    ledger.served("same-served-id", route_type="llm", ts="2026-08-21T09:00:01Z")
    ledger.receipt("same-served-id", ts="2026-08-21T09:00:02Z")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "duplicate_pending_for_served_id:same-served-id"


def test_pending_after_terminal_is_also_duplicate_pending(ledger: LedgerBuilder) -> None:
    # The canonical rule is "any pending while ANY state exists", so a
    # re-serve after a terminal is the same violation, not a fresh lifecycle.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.served("sol-0", route_type="solver", ts="2026-08-21T10:00:00Z")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "duplicate_pending_for_served_id:sol-0"


def test_receipt_terminal_without_pending_rejects(ledger: LedgerBuilder) -> None:
    # codex-lead-1 2026-08-22T04:30:52Z finding, PoC case 2 (exact shape):
    # an orphan receipt terminal followed by one valid pending+receipt was
    # silently ignored (served_total=1, evidence_complete=True). A terminal
    # for a served_id that was never served is lifecycle-invalid evidence.
    ledger.receipt("orphan-id", ts="2026-08-21T09:00:00Z")
    ledger.served("valid-id", route_type="solver", ts="2026-08-21T09:00:01Z")
    ledger.receipt("valid-id", ts="2026-08-21T09:00:02Z")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "terminal_without_pending_for_served_id:orphan-id"


def test_gap_terminal_without_pending_rejects(ledger: LedgerBuilder) -> None:
    ledger.gap("orphan-gap")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason == "terminal_without_pending_for_served_id:orphan-gap"


def test_lifecycle_rule_is_verbatim_parity_with_chat_served_accounting() -> None:
    # Lock the mirror: for every (state, entry_type) cell the tool's predicate
    # must return exactly what the canonical accounting predicate returns, so
    # the two can never drift apart silently (including the defensive
    # unknown-entry-type cell).
    state_map = {
        None: None,
        LIFECYCLE_PENDING: chat_served_accounting._PENDING,
        LIFECYCLE_RECEIPTED: chat_served_accounting._RECEIPT,
        LIFECYCLE_GAP: chat_served_accounting._GAP,
    }
    for tool_state, canonical_state in state_map.items():
        for entry_type in (SERVED_PENDING, RECEIPT_TERMINAL, GAP_TERMINAL, "bogus"):
            assert lifecycle_violation(tool_state, entry_type) == (
                chat_served_accounting._lifecycle_violation(canonical_state, entry_type)
            ), (tool_state, entry_type)


def test_unknown_entry_type_never_reaches_counting(ledger: LedgerBuilder) -> None:
    # A self-hash-consistent entry of an unknown type (append_entry itself
    # refuses it, so it is written raw) must be a structured rejection: the
    # chain verifier's well-formedness check catches it first, and the
    # lifecycle walk would reject it as unknown_entry_type if it did not.
    ledger.served("sol-0", route_type="solver")
    bogus = dict(
        new_served_pending("sol-1", ledger.prev, "2026-08-21T09:01:00Z", {"source": "chat"})
    )
    bogus["entry_type"] = "bogus"
    bogus.pop("entry_hash", None)
    bogus["entry_hash"] = compute_entry_hash(bogus)
    with ledger.path.open("ab") as handle:
        handle.write(
            (json.dumps(bogus, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
        )

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason.startswith(("chain_invalid:", "unknown_entry_type:"))


def test_chain_tamper_is_rejected(ledger: LedgerBuilder, tmp_path: Path) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["metadata"]["route_type"] = "llm"  # content change without re-hash
    lines[0] = json.dumps(first, sort_keys=True)
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    assert excinfo.value.reason.startswith("chain_invalid:")


def test_torn_tail_tolerated_and_reported(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with ledger.path.open("ab") as handle:
        handle.write(b'{"torn": tr')  # crash-shaped unparseable FINAL line

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

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
        pending_failure_ledger_path=str(ledger.failures),
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
        pending_failure_ledger_path=str(ledger.failures),
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

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["served_total"] == 2
    assert report["solver_first_served_total"] == 1
    assert report["per_route_served"] == {"solver": 1, "unknown": 1}

    narrowed = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
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
        pending_failure_ledger_path=str(ledger.failures),
        solver_route_types=("unknown",),
    )
    assert report["served_total"] == 1
    assert report["solver_first_served_total"] == 0
    assert report["solver_first_served_ratio"] == 0.0


def test_unparseable_entry_ts_under_window_rejects(ledger: LedgerBuilder) -> None:
    ledger.served("odd-ts", route_type="solver", ts="not-a-time")

    derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )  # windowless: fine

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
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

    derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )  # windowless: unaffected

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
            window_start="2026-08-21T08:00:00Z",
        )
    assert excinfo.value.reason == "entry_ts_unparseable_under_window"


def test_naive_window_bound_rejects_as_unparseable(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver", ts="2026-08-21T09:00:00Z")

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
            window_start="2026-08-21T08:00:00",
        )
    assert excinfo.value.reason == "window_start_unparseable"

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
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


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="pos-inf"),
        pytest.param(float("-inf"), id="neg-inf"),
    ],
)
def test_non_finite_telemetry_ratio_is_rejected(bad: float) -> None:
    """A NaN telemetry ratio must not read as agreement.

    NaN survives ``abs()`` and then loses every comparison, so the old
    ``delta > 1e-9`` returned False and reported the two sources as
    AGREEING. That is a false negative at an evidence-comparison
    boundary, so non-finite input fails closed instead.
    """
    report = {"solver_first_served_ratio": 0.5}
    with pytest.raises(DerivationRejected) as excinfo:
        compare_with_telemetry(report, {"solver_first_served_ratio": bad})
    assert excinfo.value.reason == "telemetry_ratio_not_finite"


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="pos-inf"),
        pytest.param(float("-inf"), id="neg-inf"),
    ],
)
def test_non_finite_report_ratio_is_rejected(bad: float) -> None:
    """Symmetric guard on the caller-supplied report mapping.

    ``derive_report`` itself cannot emit a non-finite ratio (integer
    counts, zero denominator guarded), so this defends the public
    function against a hand-built mapping rather than an internally
    reachable path.
    """
    with pytest.raises(DerivationRejected) as excinfo:
        compare_with_telemetry(
            {"solver_first_served_ratio": bad},
            {"solver_first_served_ratio": 0.5},
        )
    assert excinfo.value.reason == "report_ratio_not_finite"


def test_non_finite_is_rejected_before_any_delta_is_computed() -> None:
    """The rejection must pre-empt the subtraction, not describe it."""
    nan = float("nan")
    with pytest.raises(DerivationRejected) as excinfo:
        compare_with_telemetry(
            {"solver_first_served_ratio": nan},
            {"solver_first_served_ratio": nan},
        )
    # telemetry is validated first, so that reason wins on a both-NaN call
    assert excinfo.value.reason == "telemetry_ratio_not_finite"


def test_finite_comparison_still_reports_divergence_normally() -> None:
    """The guard must not swallow ordinary divergence."""
    report = {"solver_first_served_ratio": 0.5}
    agreeing = compare_with_telemetry(report, {"solver_first_served_ratio": 0.5})
    assert agreeing["diverges"] is False
    assert agreeing["abs_delta"] == pytest.approx(0.0)
    diverging = compare_with_telemetry(report, {"solver_first_served_ratio": 0.75})
    assert diverging["diverges"] is True
    assert diverging["abs_delta"] == pytest.approx(0.25)


def test_report_never_contains_claim_safe(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert "claim_safe" not in json.dumps(report)
    assert report["flags"]["measurement_only"] is True
    assert report["flags"]["runtime_authority_granted"] is False


def test_cli_json_and_exit_codes(
    ledger: LedgerBuilder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    assert main(
        [
            "--ledger",
            str(ledger.path),
            "--pending-failure-ledger",
            str(ledger.failures),
            "--json",
        ]
    ) == EXIT_OK
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
                "--pending-failure-ledger",
                str(ledger.failures),
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
        main(
            [
                "--ledger",
                str(ledger.path),
                "--pending-failure-ledger",
                str(ledger.failures),
                "--window-start-ts",
                "garbage",
            ]
        )
        == EXIT_REJECTED
    )
    rejection = json.loads(capsys.readouterr().out)
    assert rejection["derived"] is False
    assert rejection["reason"] == "window_start_unparseable"


# --- Durable pending-append failures: required ledger, den+gap never
# --- numerator, forced incompleteness, scope rejection (Grok-locked plan,
# --- codex-lead-1/served-ratio-pending-failure-fix-20260901).


VALID_FAILURE_LINE = json.dumps(
    {
        "schema_version": "magma.chat_served_pending_append_failure.v0",
        "reason": "sink_write_failed",
        "served_id_hash": "sha256:" + "b" * 64,
        "ts_utc": "2026-08-21T09:05:00Z",
        "metadata": {"source": "chat", "route_type": "llm"},
    },
    sort_keys=True,
)


def test_pending_failure_ledger_path_is_required_in_api(
    ledger: LedgerBuilder,
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with pytest.raises(TypeError):
        derive_report(ledger_path=str(ledger.path))  # type: ignore[call-arg]


def test_pending_failure_ledger_flag_is_required_in_cli(
    ledger: LedgerBuilder,
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with pytest.raises(SystemExit) as excinfo:
        main(["--ledger", str(ledger.path), "--json"])
    assert excinfo.value.code == 2


def test_missing_pending_failure_ledger_rejects_not_zero(
    ledger: LedgerBuilder, tmp_path: Path
) -> None:
    # The canonical helper maps a missing path to a clean zero; the tool must
    # reject BEFORE consulting it. An absent failure ledger is no evidence.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=str(tmp_path / "no_such_failures.jsonl"),
        )
    assert excinfo.value.reason == "pending_failure_ledger_not_found"


@pytest.mark.parametrize("bad_path", ["", None, 0, False])
def test_absentish_pending_failure_path_rejects_as_invalid(
    ledger: LedgerBuilder, bad_path: object
) -> None:
    # Every one of these values makes the canonical helper return 0 silently.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=bad_path,  # type: ignore[arg-type]
        )
    assert excinfo.value.reason == "pending_failure_ledger_path_invalid"


def test_directory_as_pending_failure_ledger_rejects_structurally(
    ledger: LedgerBuilder, tmp_path: Path
) -> None:
    # The canonical helper raises PermissionError on a directory; the tool
    # must produce a structured rejection instead of an uncaught exception.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    directory = tmp_path / "failures_dir"
    directory.mkdir()
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=str(directory),
        )
    assert excinfo.value.reason == "pending_failure_ledger_not_regular"


def test_symlink_to_empty_pending_failure_ledger_rejects(
    ledger: LedgerBuilder, tmp_path: Path
) -> None:
    # Path.is_file() follows links, so a symlink to an empty file would read
    # as an explicit zero; os.lstat + S_ISREG must reject the link itself.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    target = tmp_path / "real_empty.jsonl"
    target.write_text("", encoding="utf-8")
    link = tmp_path / "failures_link.jsonl"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=str(link),
        )
    assert excinfo.value.reason == "pending_failure_ledger_not_regular"


def test_failures_widen_denominator_and_gap_never_numerator(
    ledger: LedgerBuilder,
) -> None:
    # The 0.95-threshold honesty case: one receipted solver serve plus one
    # durable failed pending-append. The true solver-first ratio over served
    # responses is 0.5, not 1.0, and the window is holed.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["solver_first_served_total"] == 1
    assert report["served_total"] == 2
    assert report["solver_first_served_ratio"] == pytest.approx(0.5)
    assert report["pending_append_failures"] == 1
    assert type(report["pending_append_failures"]) is int
    assert report["receipt_coverage"] == {"receipted": 1, "gap": 1, "pending": 0}
    assert report["gapless"] is False
    assert report["evidence_complete"] is False


def test_mixed_failure_lines_all_count_without_filtering(
    ledger: LedgerBuilder,
) -> None:
    # One schema-valid line, one invalid-metadata line, one corrupt non-JSON
    # line: the canonical helper counts every nonblank durable line as a
    # failure (corrupt lines fail closed), and this tool must not re-filter.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)
    ledger.fail_pending_append(lines=1, text='{"schema_version": "wrong"}')
    ledger.fail_pending_append(lines=1, text="not json at all")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["pending_append_failures"] == 3
    assert report["served_total"] == 4
    assert report["solver_first_served_total"] == 1
    assert report["receipt_coverage"]["gap"] == 3
    assert report["evidence_complete"] is False


def test_served_total_invariant_with_failures(ledger: LedgerBuilder) -> None:
    # per_route_served stays attributable main-ledger rows only; the
    # difference from served_total is exactly the failure count.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.served("llm-0", route_type="llm")
    ledger.receipt("llm-0")
    ledger.fail_pending_append(lines=2, text=VALID_FAILURE_LINE)

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["served_total"] == sum(
        report["per_route_served"].values()
    ) + report["pending_append_failures"]
    assert report["per_route_served"] == {"llm": 1, "solver": 1}


def test_gap_terminal_with_zero_failures_keeps_evidence_complete(
    ledger: LedgerBuilder,
) -> None:
    # Failures force incompleteness; a RESOLVED gap terminal alone does not
    # (unchanged behavior, pinned so the new conjunct cannot drift wider).
    ledger.served("sol-0", route_type="solver")
    ledger.gap("sol-0")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )

    assert report["pending_append_failures"] == 0
    assert report["gapless"] is False
    assert report["evidence_complete"] is True


def test_failures_with_served_route_narrowing_reject(
    ledger: LedgerBuilder,
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=str(ledger.failures),
            served_route_types=("solver",),
        )
    assert excinfo.value.reason == "pending_append_failures_cannot_be_scoped"


def test_failures_with_time_window_reject(ledger: LedgerBuilder) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)

    with pytest.raises(DerivationRejected) as excinfo:
        derive_report(
            ledger_path=str(ledger.path),
            pending_failure_ledger_path=str(ledger.failures),
            window_start="2026-08-21T00:00:00Z",
        )
    assert excinfo.value.reason == "pending_append_failures_cannot_be_scoped"


def test_empty_failure_file_with_narrowing_still_derives(
    ledger: LedgerBuilder,
) -> None:
    # An empty-but-present failure file is an explicit zero; there is nothing
    # to mis-attribute, so narrowing stays allowed.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
        served_route_types=("solver",),
        window_start="2026-08-21T00:00:00Z",
        window_end="2026-08-21T23:59:59Z",
    )

    assert report["pending_append_failures"] == 0
    assert report["served_total"] == 1


def test_solver_route_narrowing_alone_with_failures_derives(
    ledger: LedgerBuilder,
) -> None:
    # --solver-route-type narrows numerator MEMBERSHIP only; failures never
    # enter the numerator, so nothing can be mis-attributed and the report
    # must derive rather than reject.
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
        solver_route_types=("solver", "causal"),
    )

    assert report["pending_append_failures"] == 1
    assert report["served_total"] == 2
    assert report["solver_first_served_total"] == 1


def test_no_failure_path_leak_and_no_claim_safe(
    ledger: LedgerBuilder,
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    ledger.fail_pending_append(lines=1, text=VALID_FAILURE_LINE)

    report = derive_report(
        ledger_path=str(ledger.path),
        pending_failure_ledger_path=str(ledger.failures),
    )
    encoded = json.dumps(report)

    assert str(ledger.failures) not in encoded
    assert str(ledger.path) not in encoded
    assert "claim_safe" not in encoded
    assert report["flags"]["measurement_only"] is True
    assert report["flags"]["runtime_authority_granted"] is False


def test_plain_cli_prints_failures_and_telemetry_divergence(
    ledger: LedgerBuilder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")
    snapshot = tmp_path / "telemetry.json"
    snapshot.write_text(
        json.dumps({"solver_first_served_ratio": 0.25}), encoding="utf-8"
    )

    rc = main(
        [
            "--ledger",
            str(ledger.path),
            "--pending-failure-ledger",
            str(ledger.failures),
            "--telemetry-snapshot",
            str(snapshot),
        ]
    )

    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "pending_append_failures=0" in out
    assert "telemetry_ratio=0.250000" in out
    assert "ledger_ratio=1.000000" in out
    assert "abs_delta=0.750000" in out
    assert "diverges=True" in out


def test_cli_missing_failure_ledger_is_structured_rejection(
    ledger: LedgerBuilder, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger.served("sol-0", route_type="solver")
    ledger.receipt("sol-0")

    rc = main(
        [
            "--ledger",
            str(ledger.path),
            "--pending-failure-ledger",
            str(tmp_path / "no_such_failures.jsonl"),
            "--json",
        ]
    )

    assert rc == EXIT_REJECTED
    rejection = json.loads(capsys.readouterr().out)
    assert rejection["derived"] is False
    assert rejection["reason"] == "pending_failure_ledger_not_found"
